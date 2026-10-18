"""web (uvicorn + FastAPI): the API (PROMPT.md section 7), auth, uploads, replay downloads and the static site/viewer (section 8).
Never runs model code or the simulator. Listens on 127.0.0.1:8700 (Tailscale Funnel's target) and serves /healthz only on a
second loopback listener, 127.0.0.1:8701, that Funnel never points at."""
import asyncio
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
import zlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartParser

from osoarena import auth, config, dashboard, db, ladder, replays, reports
from osoarena.text import dumps

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://avatars.githubusercontent.com; "
       "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
CSP_VIEWER = CSP.replace("script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'")
PAGE_PREFIXES = ("/", "/ladder", "/models/", "/matches/", "/tournaments", "/my-models", "/get-started", "/terms", "/privacy")
MULTIPART_OVERHEAD = 16 * 1024
TYPES = {".wasm": "application/wasm", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".json": "application/json",
         ".svg": "image/svg+xml", ".woff2": "font/woff2", ".gltf": "model/gltf+json", ".bin": "application/octet-stream",
         ".txt": "text/plain; charset=utf-8", ".png": "image/png", ".webmanifest": "application/manifest+json"}
MAX_APP_SESSIONS = 5000  # stored app session reports per user
APP_PUTS_PER_MIN = 60  # PUT /api/me/sessions/{id} + PUT /api/me/style, per user
APP_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # starts alphanumeric: no "." / ".." path segments
STARTED_AT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,9})?Z")  # fractional seconds are dropped on store
MAX_REPORT = 256 * 1024  # a stored volante-report/1 (the stroke breakdown can be big)
COMPRESSIBLE = {".wasm", ".js", ".mjs", ".css", ".html", ".json", ".svg", ".gltf", ".bin", ".txt"}


class ApiError(Exception):
    def __init__(self, status, code, detail, headers=None):
        self.status, self.code, self.detail, self.headers = status, code, detail, headers or {}


def err(status, code, detail, headers=None):
    h = {"cache-control": "no-store", **(headers or {})}
    return JSONResponse({"error": code, "detail": detail}, status_code=status, headers=h)


# ---------------------------------------------------------------- per-thread DB connection

_local = threading.local()


def conn():
    c = getattr(_local, "con", None)
    if c is None:
        c = _local.con = db.connect()
    return c


# ---------------------------------------------------------------- rate limits (in memory: web is one process)

class Limiter:
    def __init__(self):
        self.win = defaultdict(lambda: [0, 0.0])
        self.lock = threading.Lock()

    def hit(self, key, limit, window=60.0):
        """-> 0 if allowed, else seconds until the window resets."""
        now = time.monotonic()
        with self.lock:
            w = self.win[key]
            if now - w[1] >= window:
                w[0], w[1] = 0, now
            w[0] += 1
            if w[0] > limit:
                return max(1, int(window - (now - w[1])) + 1)
            if len(self.win) > 50000:  # bound memory under a flood of distinct keys
                for k in [k for k, v in self.win.items() if now - v[1] >= window]:
                    del self.win[k]
            return 0


limiter = Limiter()


def client_ip(request: Request):
    """The forwarding header is trusted only when the TCP peer is loopback (the local Funnel proxy). Rightmost value = the one the
    proxy itself appended; anything a client sent is to its left."""
    cfg = config.get()
    peer = request.client.host if request.client else ""
    if cfg.client_ip_header and peer in ("127.0.0.1", "::1"):
        v = request.headers.get(cfg.client_ip_header)
        if v:
            ip = v.split(",")[-1].strip()
            if 0 < len(ip) <= 64:
                return ip
    return peer or "?"


# ---------------------------------------------------------------- static files (precompressed in memory at startup)

class Static:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.cache = {}
        if self.root.is_dir():
            for p in self.root.rglob("*"):
                if p.is_file():
                    self._load(p)

    def _load(self, p):
        data = p.read_bytes()
        ext = p.suffix.lower()
        gz = gzip.compress(data, 9, mtime=0) if ext in COMPRESSIBLE and len(data) > 1024 else None
        if gz is not None and len(gz) >= len(data) * 0.9:
            gz = None
        etag = '"' + hashlib.sha256(data).hexdigest()[:20] + '"'
        ctype = TYPES.get(ext) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        self.cache[str(p.relative_to(self.root))] = (data, gz, etag, ctype)

    def get(self, rel):
        return self.cache.get(rel.lstrip("/"))

    def respond(self, request, rel, cache_control, csp):
        hit = self.get(rel)
        if not hit:
            return None
        data, gz, etag, ctype = hit
        h = {"cache-control": cache_control, "etag": etag, "content-security-policy": csp, "vary": "accept-encoding"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=h)
        body = data
        if gz is not None and "gzip" in request.headers.get("accept-encoding", ""):
            body, h["content-encoding"] = gz, "gzip"
        if request.method == "HEAD":
            h["content-length"] = str(len(body))
            return Response(status_code=200, headers=h, media_type=ctype)
        return Response(body, headers=h, media_type=ctype)


# ---------------------------------------------------------------- serialisers (shapes of docs/site-types.ts)

def owner_of(con, m):
    if m["house"]:
        return {"house": True}
    if not m["user_id"]:
        return dict(db.DELETED_USER)
    u = con.execute("SELECT id, login, avatar_url FROM users WHERE id=?", (m["user_id"],)).fetchone()
    return {"id": u["id"], "login": u["login"], "avatar_url": u["avatar_url"]} if u else dict(db.DELETED_USER)


def twin_of(m):
    """ModelSummary.twin (section 14): the stored profile and the last fidelity verdict; None for a model without a profile."""
    if not m["style_profile"]:
        return None
    try:
        profile = json.loads(m["style_profile"])
        rep = json.loads(m["fidelity"]) if m["fidelity"] else None
    except ValueError:
        return None
    fid = {"ok": bool(rep and rep.get("fidelity_ok")), "tempo_ratio": rep.get("tempo_ratio") if rep else None,
           "mix_js": rep.get("mix_js") if rep else None, "checked_at": m["fidelity_at"] if rep else None}
    if rep:
        fid["reasons"] = [str(x) for x in rep.get("reasons") or []]
    return {"profile": profile, "fidelity": fid}


def model_summary(con, m, lad=None):
    """`lad`: whose record to show (a house player is on both ladders); default the model's own ladder."""
    lad = lad or ("open" if m["house"] else m["ladder"] or "open")
    rc, gc = ladder.COLS[lad][:2]
    rating = None if m["status"] in ("pending", "rejected") or m[rc] is None else round(m[rc], 1)
    return {"id": m["id"], "name": m["name"], "owner": owner_of(con, m), "build": ladder.build_of(m), "status": m["status"],
            "rating": rating, "games": m[gc], "ladder": m["ladder"] or "open", "twin": twin_of(m)}


def model_detail(con, m):
    d = model_summary(con, m)
    hist = con.execute("SELECT t, rating, match_id FROM (SELECT rowid AS rid, * FROM rating_history WHERE model_id=? AND ladder=? "
                       "ORDER BY rowid DESC LIMIT 200) ORDER BY rid ASC", (m["id"], d["ladder"] if not m["house"] else "open")).fetchall()
    d.update(description=m["description"], created_at=m["created_at"], retired_at=m["retired_at"], season_id=m["season_id"],
             rating_history=[{"t": h["t"], "rating": round(h["rating"], 1), "match_id": h["match_id"]} for h in hist])
    if m["status"] == "rejected":
        d["reject_reasons"] = json.loads(m["reject_reasons"] or "[]")
    return d


def season_json(s):
    return {"id": s["id"], "contract": s["contract"], "kit_version": s["kit_version"], "started_at": s["started_at"],
            "ended_at": s["ended_at"], "active": bool(s["active"])}


def match_summary(con, mt):
    a = con.execute("SELECT * FROM models WHERE id=?", (mt["a_id"],)).fetchone()
    b = con.execute("SELECT * FROM models WHERE id=?", (mt["b_id"],)).fetchone()
    done = mt["status"] == "done"
    return {"id": mt["id"], "kind": mt["kind"], "season_id": mt["season_id"], "created_at": mt["created_at"],
            "finished_at": mt["finished_at"], "status": mt["status"], "ladder": mt["ladder"] or "open",
            "a": model_summary(con, a, mt["ladder"]), "b": model_summary(con, b, mt["ladder"]),
            "games_won": json.loads(mt["games_won"]) if mt["games_won"] else [0, 0],
            "winner": mt["winner"] if done else None,
            "games": json.loads(mt["games"]) if mt["games"] else [],
            "rating_delta": json.loads(mt["rating_delta"]) if done and mt["rating_delta"] and mt["kind"] != "tournament" else None}


def tournament_json(con, t, full=True):
    lad = t["ladder"] or "open"
    d = {"id": t["id"], "season_id": t["season_id"], "starts_at": t["starts_at"], "status": t["status"], "ladder": lad}
    if full:
        cache = {}

        def ms(mid):
            if mid is None:
                return None
            if mid not in cache:
                cache[mid] = model_summary(con, con.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone(), lad)
            return cache[mid]
        rounds = json.loads(t["rounds"]) if t["status"] != "scheduled" else []
        d["rounds"] = [[{"seed_a": tie["seed_a"], "seed_b": tie["seed_b"], "a": ms(tie["a"]), "b": ms(tie["b"]),
                         "match_id": tie["match_id"], "winner": tie["winner"]} for tie in r] for r in rounds]
    return d


# ---------------------------------------------------------------- app

def create_app():
    cfg = config.get()
    for d in (cfg.models_dir, cfg.quarantine_dir, cfg.logs_dir, cfg.replays_dir, cfg.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    c0 = db.connect()
    db.init(c0)
    c0.close()
    auth._secret()  # fail fast without a session secret
    site, viewer = Static(cfg.site_dir), Static(cfg.viewer_dir)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    # ---- request context
    def current(request):
        """-> (user row | None, csrf expected)"""
        if not hasattr(request.state, "user"):
            u, csrf = auth.session_user(conn(), request.cookies.get(auth.SESSION_COOKIE))
            request.state.user, request.state.csrf = u, csrf
        return request.state.user, request.state.csrf

    def require_user(request, csrf=True):
        u, expected = current(request)
        if not u:
            raise ApiError(401, "auth", "Please sign in first.")
        if csrf and not auth.csrf_ok(expected, request.headers.get("x-csrf-token")):
            raise ApiError(403, "csrf", "Missing or invalid CSRF token. Reload the page and try again.")
        return u

    def uploads_used(con, user_id):
        since = db.fmt_t(db.parse_t(db.now()) - __import__("datetime").timedelta(days=1))
        rows = con.execute("SELECT created_at FROM models WHERE uploaded_by=? AND created_at>? ORDER BY created_at",
                           (user_id, since)).fetchall()
        return rows

    def set_cookie(resp, name, value, max_age, path="/"):
        resp.set_cookie(name, value, max_age=max_age, path=path, httponly=True, secure=cfg.cookie_secure, samesite="lax")

    @app.exception_handler(ApiError)
    async def _api_error(request, e: ApiError):
        return err(e.status, e.code, e.detail, e.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request, e):
        codes = {400: "bad_request", 401: "auth", 403: "forbidden", 404: "not_found", 405: "bad_request", 413: "too_large"}
        return err(e.status_code, codes.get(e.status_code, "internal"), "Not found." if e.status_code == 404 else "Bad request.")

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def _validation(request, e):
        return err(400, "bad_request", "That request wasn't valid.")

    @app.exception_handler(Exception)
    async def _crash(request, e):
        print(f"500 {request.method} {request.url.path}: {type(e).__name__}: {e}", flush=True)
        return err(500, "internal", "Something went wrong on the server.")

    # ---- middleware: rate limits, security headers, access log (no query strings: OAuth codes stay out of logs)
    @app.middleware("http")
    async def _mw(request: Request, call_next):
        t0 = time.monotonic()
        ip = client_ip(request)
        path = request.url.path
        request.state.ip = ip
        if path.startswith("/api/") or path.startswith("/auth/"):
            replay = path.endswith("/replay.bin")
            write = request.method not in ("GET", "HEAD")
            wait = limiter.hit("global", cfg.rate_global)
            if not wait:
                if replay:
                    wait = limiter.hit("replay:" + ip, cfg.rate_replay)
                elif write:
                    wait = limiter.hit("w:" + ip, cfg.rate_writes)
                else:
                    wait = limiter.hit("r:" + ip, cfg.rate_reads)
            if wait:
                resp = err(429, "rate_limited", "Too many requests. Try again in a minute.", {"retry-after": str(wait)})
                return _finish(request, resp, t0)
        try:
            resp = await call_next(request)
        except Exception as e:  # noqa: BLE001
            print(f"500 {request.method} {path}: {type(e).__name__}: {e}", flush=True)
            resp = err(500, "internal", "Something went wrong on the server.")
        return _finish(request, resp, t0)

    def _finish(request, resp, t0):
        h = resp.headers
        path = request.url.path
        h.setdefault("content-security-policy", CSP_VIEWER if path.startswith("/viewer/") else CSP)
        h["x-content-type-options"] = "nosniff"
        h["referrer-policy"] = "strict-origin-when-cross-origin"
        h["x-frame-options"] = "SAMEORIGIN"
        if cfg.hsts:
            h["strict-transport-security"] = "max-age=31536000"
        if path.startswith("/api/") and "cache-control" not in h:
            h["cache-control"] = "no-store"
        for k in [k for k in h.keys() if k.lower().startswith("access-control-")]:
            del h[k]
        print(f'{request.state.ip if hasattr(request.state, "ip") else "?"} {request.method} {path} {resp.status_code} '
              f'{(time.monotonic() - t0) * 1000:.0f}ms', flush=True)
        return resp

    # ---- public reads
    @app.get("/api/me")
    def me(request: Request):
        u, csrf = current(request)
        if u:
            con = conn()
            used = len(uploads_used(con, u["id"]))
            # app_linked: signed in from the app, or has sessions (uploaded by a build before that was recorded)
            linked = bool(u["app_linked_at"]) or bool(con.execute("SELECT 1 FROM play_sessions WHERE user_id=? LIMIT 1", (u["id"],)).fetchone())
            body = {"user": {"id": u["id"], "login": u["login"], "avatar_url": u["avatar_url"]}, "csrf": csrf,
                    "uploads_left_today": max(0, cfg.daily_uploads - used), "app_linked": linked}
            return JSONResponse(body, headers={"cache-control": "no-store"})
        anon = request.cookies.get(auth.ANON_COOKIE)
        fresh = not anon or len(anon) > 64
        if fresh:
            anon = secrets.token_urlsafe(24)
        resp = JSONResponse({"user": None, "csrf": auth.anon_csrf(anon)}, headers={"cache-control": "no-store"})
        if fresh:
            set_cookie(resp, auth.ANON_COOKIE, anon, 86400 * 30)
        return resp

    @app.get("/api/seasons")
    def seasons():
        con = conn()
        return [season_json(s) for s in con.execute("SELECT * FROM seasons ORDER BY id DESC")]

    def ladder_param(q):
        lad = q.get("ladder") or "open"
        if lad not in db.LADDERS:
            raise ApiError(400, "bad_request", "ladder must be open or twins.")
        return lad

    def int_param(v, name, default, lo=None, hi=None):
        if v is None or v == "":
            return default
        try:
            n = int(v)
        except ValueError:
            raise ApiError(400, "bad_request", f"{name} must be an integer.")
        if lo is not None and n < lo:
            raise ApiError(400, "bad_request", f"{name} is out of range.")
        return min(n, hi) if hi is not None else n

    @app.get("/api/ladder")
    def ladder_(request: Request):
        con = conn()
        q = request.query_params
        sid = int_param(q.get("season"), "season", None)
        season = con.execute("SELECT * FROM seasons WHERE id=?", (sid,)).fetchone() if sid else db.active_season(con)
        if not season:
            raise ApiError(404, "not_found", "No such season.")
        offset = int_param(q.get("offset"), "offset", 0, 0)
        limit = int_param(q.get("limit"), "limit", 50, 1, 100)
        lad = ladder_param(q)
        rc, gc, wc, lc = ladder.COLS[lad]
        # the house players are on both ladders; everyone else only on their own (open never lists twins and vice versa)
        where = "season_id=? AND status='active' AND (house IS NOT NULL OR ladder=?)"
        total = con.execute(f"SELECT COUNT(*) c FROM models WHERE {where}", (season["id"], lad)).fetchone()["c"]
        rows = con.execute(f"SELECT * FROM models WHERE {where} ORDER BY COALESCE({rc}, 1200) DESC, id ASC LIMIT ? OFFSET ?",
                           (season["id"], lad, limit, offset)).fetchall()
        items = [{"rank": offset + i + 1, "model": model_summary(con, m, lad), "rating": round(ladder.rating(m, lad), 1),
                  "provisional": m[gc] < cfg.provisional_games, "games": m[gc], "wins": m[wc], "losses": m[lc]}
                 for i, m in enumerate(rows)]
        return {"season": season_json(season), "ladder": lad, "total": total, "items": items}

    def visible_model(request, model_id):
        con = conn()
        m = con.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        if not m:
            return None
        if m["status"] in ("pending", "rejected", "hidden"):
            u, _ = current(request)
            if not u or u["id"] != m["user_id"]:
                return None
        return m

    @app.get("/api/models/{model_id}")
    def model_get(model_id: int, request: Request):
        m = visible_model(request, model_id)
        if not m:
            raise ApiError(404, "not_found", "No such model.")
        h = {"cache-control": "no-store"}
        return JSONResponse(model_detail(conn(), m), headers=h)

    @app.get("/api/models")
    def my_models(request: Request):
        u = require_user(request, csrf=False)
        if request.query_params.get("owner") != "me":
            raise ApiError(400, "bad_request", "Only owner=me is supported.")
        con = conn()
        rows = con.execute("SELECT * FROM models WHERE user_id=? ORDER BY id DESC LIMIT 200", (u["id"],)).fetchall()
        return JSONResponse({"items": [model_summary(con, m) for m in rows]}, headers={"cache-control": "no-store"})

    def match_visible(con, mt):
        if not mt or mt["status"] == "error":
            return False
        hid = con.execute("SELECT 1 FROM models WHERE id IN (?, ?) AND status='hidden'", (mt["a_id"], mt["b_id"])).fetchone()
        return not hid

    @app.get("/api/matches")
    def matches(request: Request):
        con = conn()
        q = request.query_params
        limit = int_param(q.get("limit"), "limit", 20, 1, 100)
        where, args = ["m.status IN ('queued','running','done','void')",
                       "NOT EXISTS (SELECT 1 FROM models h WHERE h.id IN (m.a_id, m.b_id) AND h.status='hidden')"], []
        model = int_param(q.get("model"), "model", None)
        if model is not None:
            where.append("(m.a_id=? OR m.b_id=?)")
            args += [model, model]
        else:
            where.append("m.season_id=?")
            args.append(db.active_season(con)["id"])
        kind = q.get("kind")
        if kind:
            if kind not in ("ladder", "placement", "tournament"):
                raise ApiError(400, "bad_request", "Unknown match kind.")
            where.append("m.kind=?")
            args.append(kind)
        before = int_param(q.get("before"), "before", None)
        if before is not None:
            where.append("m.id<?")
            args.append(before)
        rows = con.execute(f"SELECT m.* FROM matches m WHERE {' AND '.join(where)} ORDER BY m.id DESC LIMIT ?",
                           (*args, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": [match_summary(con, r) for r in rows], "next_before": rows[-1]["id"] if more and rows else None}

    @app.get("/api/matches/{match_id}")
    def match_get(match_id: int):
        con = conn()
        mt = con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if not match_visible(con, mt):
            raise ApiError(404, "not_found", "No such match.")
        d = match_summary(con, mt)
        d["seed"] = mt["seed"]
        d["rallies"] = json.loads(mt["rallies"]) if mt["status"] == "done" and mt["rallies"] else []
        return d

    @app.get("/api/matches/{match_id}/replay.bin")
    def replay(match_id: int, request: Request):
        con = conn()
        mt = con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if not match_visible(con, mt) or mt["status"] != "done":
            raise ApiError(404, "not_found", "This match has no replay.")
        state, x = replays.request(con, match_id)
        if state == "building":
            return JSONResponse({"status": "building"}, status_code=202, headers={"retry-after": "3", "cache-control": "no-store"})
        if state == "busy":
            raise ApiError(503, "busy", "Too many replays are being prepared. Try again in a minute.", {"retry-after": "30"})
        if state == "failed":
            raise ApiError(500, "replay_failed", x)
        h = {"cache-control": "public, max-age=86400", "vary": "accept-encoding",
             "content-security-policy": CSP}
        if "gzip" in request.headers.get("accept-encoding", ""):
            h["content-encoding"] = "gzip"
            return FileResponse(x, media_type="application/octet-stream", headers=h)

        def inflate():
            d = zlib.decompressobj(31)
            with open(x, "rb") as f:
                while chunk := f.read(1 << 16):
                    yield d.decompress(chunk)
            yield d.flush()
        return StreamingResponse(inflate(), media_type="application/octet-stream", headers=h)

    @app.get("/api/tournaments/current")
    def t_current(request: Request):
        con = conn()
        lad = ladder_param(request.query_params)
        t = con.execute("SELECT * FROM tournaments WHERE status='running' AND ladder=? ORDER BY id DESC LIMIT 1", (lad,)).fetchone() or \
            con.execute("SELECT * FROM tournaments WHERE status='scheduled' AND ladder=? ORDER BY starts_at ASC LIMIT 1", (lad,)).fetchone()
        if not t:
            raise ApiError(404, "not_found", "No tournament is scheduled yet.")
        return tournament_json(con, t)

    @app.get("/api/tournaments")
    def t_list(request: Request):
        con = conn()
        sid = int_param(request.query_params.get("season"), "season", None)
        sql, args = "SELECT * FROM tournaments WHERE status!='skipped' AND ladder=?", [ladder_param(request.query_params)]
        if sid is not None:
            sql += " AND season_id=?"
            args.append(sid)
        rows = con.execute(sql + " ORDER BY starts_at DESC, id DESC LIMIT 200", args).fetchall()
        return [tournament_json(con, t) for t in rows]

    @app.get("/api/tournaments/{tid}")
    def t_get(tid: int):
        con = conn()
        t = con.execute("SELECT * FROM tournaments WHERE id=? AND status!='skipped'", (tid,)).fetchone()
        if not t:
            raise ApiError(404, "not_found", "No such tournament.")
        return tournament_json(con, t)

    # ---- signed-in writes
    @app.post("/api/models")
    async def upload(request: Request):
        u = await asyncio.to_thread(require_user, request)
        con = conn()
        used = await asyncio.to_thread(uploads_used, con, u["id"])
        if len(used) >= cfg.daily_uploads:
            oldest = db.parse_t(used[0]["created_at"])
            wait = max(60, int((oldest - db.parse_t(db.now())).total_seconds() + 86400))
            raise ApiError(429, "rate_limited", f"You can upload {cfg.daily_uploads} models per day.", {"retry-after": str(wait)})
        cap = cfg.max_upload + MULTIPART_OVERHEAD
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            raise ApiError(400, "bad_request", "Bad Content-Length.")
        too_large = ApiError(413, "too_large", "That file is too large: the limit is 1 MiB (1,048,576 bytes).", {"connection": "close"})
        if declared > cap:
            raise too_large
        body = bytearray()
        try:
            async with asyncio.timeout(60):
                async for chunk in request.stream():
                    body += chunk
                    if len(body) > cap:
                        raise too_large
        except TimeoutError:
            raise ApiError(400, "bad_request", "The upload took too long.", {"connection": "close"})
        ctype = request.headers.get("content-type", "")
        if not ctype.startswith("multipart/form-data"):
            raise ApiError(400, "bad_request", "Send the file as multipart/form-data with a part named file.")

        async def once():
            yield bytes(body)
            yield b""
        try:
            form = await MultiPartParser(request.headers, once(), max_files=1, max_fields=4).parse()
        except Exception:  # noqa: BLE001 -- malformed multipart
            raise ApiError(400, "bad_request", "Malformed upload.")
        f = form.get("file")
        if not isinstance(f, UploadFile):
            raise ApiError(400, "bad_request", "The form has no part named file.")
        data = await f.read(cfg.max_upload + 1)
        await form.close()
        if len(data) > cfg.max_upload:
            raise too_large
        if not data:
            raise ApiError(400, "bad_request", "The file is empty.")
        if shutil.disk_usage(cfg.data_dir).free < cfg.min_free_bytes:
            raise ApiError(503, "busy", "The server is low on disk space. Try again later.")
        return await asyncio.to_thread(_store_upload, u, data)

    def _store_upload(u, data):
        con = conn()
        sha = hashlib.sha256(data).hexdigest()
        dst = cfg.models_dir / f"{sha}.safetensors"
        if not dst.exists():
            tmp = cfg.quarantine_dir / (secrets.token_hex(8) + ".part")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, 0o640)
            # a rename keeps the group quarantine/ gave the file; give it models/ group (oso-arena-sandbox) so the child can read it
            os.chown(tmp, -1, os.stat(cfg.models_dir).st_gid)
            os.replace(tmp, dst)
        with db.tx(con):
            season = db.active_season(con)
            cur = con.execute("INSERT INTO models (season_id, user_id, sha256, name, description, build, status, created_at, uploaded_by) "
                              "VALUES (?, ?, ?, '(checking)', NULL, ?, 'pending', ?, ?)",
                              (season["id"], u["id"], sha, dumps(db.PLACEHOLDER_BUILD), db.now(), u["id"]))
            mid = cur.lastrowid
            dup = ladder.duplicate_reason(con, season["id"], sha, mid)
            if dup:
                con.execute("UPDATE models SET status='rejected', name='(rejected upload)', reject_reasons=? WHERE id=?",
                            (dumps([dup]), mid))
            else:
                db.enqueue(con, "check", mid)
        return JSONResponse({"id": mid, "status": "pending"}, status_code=202, headers={"cache-control": "no-store"})

    @app.delete("/api/models/{model_id}")
    def retire(model_id: int, request: Request):
        u = require_user(request)
        con = conn()
        with db.tx(con):
            m = con.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
            if not m or (m["status"] in ("pending", "rejected", "hidden") and m["user_id"] != u["id"]):
                raise ApiError(404, "not_found", "No such model.")
            if m["user_id"] != u["id"]:
                raise ApiError(403, "forbidden", "That isn't yours.")
            if m["status"] != "active":
                raise ApiError(400, "bad_request", "Only an active model can be retired.")
            ladder.retire(con, m["id"])
        return Response(status_code=204)

    @app.delete("/api/me")
    def delete_me(request: Request):
        u = require_user(request)
        con = conn()
        files = []
        with db.tx(con):
            for m in con.execute("SELECT * FROM models WHERE user_id=?", (u["id"],)).fetchall():
                if m["status"] == "active":
                    ladder.retire(con, m["id"])
                elif m["status"] == "pending":
                    con.execute("DELETE FROM jobs WHERE kind='check' AND ref_id=? AND status='queued'", (m["id"],))
                    con.execute("UPDATE models SET status='rejected', reject_reasons=? WHERE id=?",
                                (dumps(["the account was deleted"]), m["id"]))
                if m["sha256"]:
                    files.append(m["sha256"])
            con.execute("UPDATE models SET user_id=0, uploaded_by=NULL WHERE user_id=?", (u["id"],))
            con.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
            con.execute("DELETE FROM pods WHERE user_id=?", (u["id"],))
            con.execute("DELETE FROM play_sessions WHERE user_id=?", (u["id"],))
            con.execute("DELETE FROM app_style WHERE user_id=?", (u["id"],))
            con.execute("DELETE FROM users WHERE id=?", (u["id"],))
            keep = {r["sha256"] for r in con.execute("SELECT sha256 FROM models WHERE user_id!=0 AND sha256 IS NOT NULL")}
        for sha in set(files) - keep:
            try:
                (cfg.models_dir / f"{sha}.safetensors").unlink()
            except FileNotFoundError:
                pass
        resp = Response(status_code=204)
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    # ---------------------------------------------------------------- pods (site Settings; pairing from the app is future work)

    def pod_out(p):
        return {"id": f"pod_{p['id']}", "hw_id": p["hw_id"], "name": p["name"], "last_seen": p["last_seen"]}

    @app.get("/api/me/pods")
    def my_pods(request: Request):
        u = require_user(request, csrf=False)
        rows = conn().execute("SELECT * FROM pods WHERE user_id=? ORDER BY id", (u["id"],)).fetchall()
        return JSONResponse({"items": [pod_out(p) for p in rows]}, headers={"cache-control": "no-store"})

    @app.patch("/api/me/pods/{pod_id}")
    async def rename_pod(pod_id: str, request: Request):
        u = require_user(request)
        try:
            name = (await request.json()).get("name")
        except Exception:  # noqa: BLE001 -- any unreadable body is a bad name
            name = None
        name = name.strip() if isinstance(name, str) else ""
        if not 1 <= len(name) <= 40 or any(unicodedata.category(ch).startswith("C") for ch in name):
            raise ApiError(400, "invalid_name", "A pod name is 1-40 characters, without control characters.")
        m = re.fullmatch(r"pod_(\d{1,12})", pod_id)
        con = conn()
        with db.tx(con):
            p = m and con.execute("SELECT * FROM pods WHERE id=? AND user_id=?", (int(m[1]), u["id"])).fetchone()
            if not p:
                raise ApiError(404, "not_found", "No such pod.")
            con.execute("UPDATE pods SET name=? WHERE id=?", (name, p["id"]))
            p = con.execute("SELECT * FROM pods WHERE id=?", (p["id"],)).fetchone()
        return JSONResponse(pod_out(p), headers={"cache-control": "no-store"})

    # ---------------------------------------------------------------- play sessions + dashboard (the app uploads each finished session)

    def no_store(body, status=200):
        return JSONResponse(body, status_code=status, headers={"cache-control": "no-store"})

    def app_session_id(sid):
        if not APP_SESSION_ID.fullmatch(sid):
            raise ApiError(400, "bad_request", "A session id is 1-64 characters of letters, digits, . _ -, starting with a letter or digit.")

    def app_put_limit(u):
        wait = limiter.hit(f"app_put:{u['id']}", APP_PUTS_PER_MIN)
        if wait:
            raise ApiError(429, "rate_limited", "Too many uploads. Try again in a minute.", {"retry-after": str(wait)})

    async def body_capped(request, cap):
        """The raw body, refused with 413 when its declared length or the bytes read pass `cap` (before any JSON parsing)."""
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            raise ApiError(400, "bad_request", "Bad Content-Length.") from None
        if declared > cap:
            raise ApiError(413, "too_large", f"The body is over the {cap}-byte limit.", {"connection": "close"})
        raw = bytearray()
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > cap:
                raise ApiError(413, "too_large", f"The body is over the {cap}-byte limit.")
        return bytes(raw)

    def _no_constant(c):
        raise ValueError(f"{c} is not allowed")

    def stored_report(r):
        """A row's report, or None when it has none or it fails today's validator (rows stored before it)."""
        if not r["report"]:
            return None
        try:
            return reports.validate_report(json.loads(r["report"]))
        except (ValueError, RecursionError):
            return None

    def play_out(r, full=False):
        o = {"id": r["id"], "started_at": r["started_at"], "sensor": r["sensor"], "duration_s": r["duration_s"],
             "swings": r["swings"], "hits": r["hits"], "best_kmh": r["best_kmh"], "avg_bpm": r["avg_bpm"],
             "uploaded_at": r["uploaded_at"]}
        if full:
            o["report"] = stored_report(r)
        return o

    def num(v, lo, hi, name, optional=False):
        if v is None and optional:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ApiError(400, "invalid_session", f"{name} is missing or out of range.")
        return v

    @app.put("/api/me/sessions/{sid}")
    async def put_session(sid: str, request: Request):
        """Body {started_at, sensor: pod|watch|pod+watch, duration_s, swings, hits, best_kmh?, avg_bpm?, report?: volante-report/1}.
        Idempotent upsert by (user, id) -> 200 PlaySession. Errors: 400 bad_request | invalid_session | invalid_report |
        too_many_sessions, 413 too_large, 429 rate_limited."""
        u = await asyncio.to_thread(require_user, request)
        app_put_limit(u)
        app_session_id(sid)
        raw = await body_capped(request, MAX_REPORT + 4096)
        return await asyncio.to_thread(store_play_session, u, sid, raw)

    def store_play_session(u, sid, raw):
        try:
            b = json.loads(raw, parse_constant=_no_constant)
        except (ValueError, RecursionError):
            b = None
        if not isinstance(b, dict):
            raise ApiError(400, "invalid_session", "The body must be a JSON object.")
        started = b.get("started_at")
        if not isinstance(started, str) or not STARTED_AT.fullmatch(started):
            raise ApiError(400, "invalid_session", "started_at must be an ISO 8601 UTC time.")
        started = started[:19] + "Z"  # whole seconds: one sortable format for the list cursor
        try:
            datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise ApiError(400, "invalid_session", "started_at is not a real date.") from None
        sensor = b.get("sensor")
        if sensor not in ("pod", "watch", "pod+watch"):
            raise ApiError(400, "invalid_session", "sensor must be pod, watch or pod+watch.")
        rep = None
        if b.get("report") is not None:
            try:
                rep = dumps(reports.validate_report(b["report"]))
            except (ValueError, RecursionError) as e:
                raise ApiError(400, "invalid_report", str(e)[:300] or "invalid report") from None
            if len(rep) > MAX_REPORT:
                raise ApiError(413, "too_large", "The report is too large.")
        row = (sid, u["id"], started, sensor, num(b.get("duration_s"), 0, 86400, "duration_s"),
               int(num(b.get("swings"), 0, 100000, "swings")), int(num(b.get("hits"), 0, 100000, "hits")),
               num(b.get("best_kmh"), 0, 1000, "best_kmh", True), num(b.get("avg_bpm"), 20, 260, "avg_bpm", True), rep, db.now())
        con = conn()
        with db.tx(con):
            known = con.execute("SELECT 1 FROM play_sessions WHERE user_id=? AND id=?", (u["id"], sid)).fetchone()
            if not known and con.execute("SELECT COUNT(*) FROM play_sessions WHERE user_id=?", (u["id"],)).fetchone()[0] >= MAX_APP_SESSIONS:
                raise ApiError(400, "too_many_sessions", f"At most {MAX_APP_SESSIONS} sessions are kept. Delete some first.")
            con.execute("INSERT OR REPLACE INTO play_sessions (id, user_id, started_at, sensor, duration_s, swings, hits, best_kmh, "
                        "avg_bpm, report, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
            # the app may not have signed in through the exchange that sets it (an older build)
            con.execute("UPDATE users SET app_linked_at=? WHERE id=? AND app_linked_at IS NULL", (db.now(), u["id"]))
            r = con.execute("SELECT * FROM play_sessions WHERE user_id=? AND id=?", (u["id"], sid)).fetchone()
        return no_store(play_out(r))

    @app.get("/api/me/sessions")
    def my_sessions(request: Request):
        """?before=<cursor>&limit=1-500 (default 50), newest first -> {"items": [PlaySession], "next_before": str | null}.
        next_before is the opaque cursor for the next (older) page ("<started_at>~<id>"), null on the last page;
        a bare started_at like 2026-10-17T18:00:00Z is accepted too (sessions strictly before it)."""
        u = require_user(request, csrf=False)
        before, limit = request.query_params.get("before"), request.query_params.get("limit", "50")
        t, _, after_id = (before or "9999-12-31T23:59:59Z~").partition("~")
        if (not re.fullmatch(r"\d{1,3}", limit) or not 1 <= int(limit) <= 500 or not reports.TS.fullmatch(t)
                or (after_id and not APP_SESSION_ID.fullmatch(after_id)) or (before and "~" in before and not after_id)):
            raise ApiError(400, "bad_request", "limit is 1-500 and before is a next_before cursor or a started_at like 2026-10-17T18:00:00Z.")
        n = int(limit)
        # a bare timestamp means "before that second"; "" sorts below every id, so (t, "") excludes the whole second
        rows = conn().execute("SELECT * FROM play_sessions WHERE user_id=? AND (started_at, id) < (?, ?) "
                              "ORDER BY started_at DESC, id DESC LIMIT ?", (u["id"], t, after_id, n + 1)).fetchall()
        last = rows[n - 1] if len(rows) > n else None
        return no_store({"items": [play_out(x) for x in rows[:n]], "next_before": f"{last['started_at']}~{last['id']}" if last else None})

    @app.get("/api/me/sessions/{sid}")
    def my_session(sid: str, request: Request):
        """-> PlaySession + {"report": volante-report/1 | null}; 404 when the caller has no session with that id."""
        u = require_user(request, csrf=False)
        app_session_id(sid)
        r = conn().execute("SELECT * FROM play_sessions WHERE user_id=? AND id=?", (u["id"], sid)).fetchone()
        if not r:
            raise ApiError(404, "not_found", "No such session.")
        return no_store(play_out(r, full=True))

    @app.delete("/api/me/sessions/{sid}")
    def delete_session(sid: str, request: Request):
        """-> 204; 404 when the caller has no session with that id."""
        u = require_user(request)
        app_session_id(sid)
        con = conn()
        with db.tx(con):
            if not con.execute("DELETE FROM play_sessions WHERE user_id=? AND id=?", (u["id"], sid)).rowcount:
                raise ApiError(404, "not_found", "No such session.")
        return Response(status_code=204, headers={"cache-control": "no-store"})

    @app.delete("/api/me/sessions")
    def delete_app_data(request: Request):
        """"Delete my web data": all of the caller's sessions and the style profile -> 204."""
        u = require_user(request)
        con = conn()
        with db.tx(con):
            con.execute("DELETE FROM play_sessions WHERE user_id=?", (u["id"],))
            con.execute("DELETE FROM app_style WHERE user_id=?", (u["id"],))
        return Response(status_code=204, headers={"cache-control": "no-store"})

    @app.put("/api/me/style")
    async def put_app_style(request: Request):
        """Body = a volante-style/1 profile (oso_arena.style rules, <= 16 KB) -> 200 {"ok": true}. 400 invalid_profile."""
        u = await asyncio.to_thread(require_user, request)
        app_put_limit(u)
        raw = await body_capped(request, reports.MAX_BYTES)
        return await asyncio.to_thread(store_app_style, u, raw)

    def store_app_style(u, raw):
        from oso_arena import style
        try:
            profile = style.parse_profile(raw.decode())
        except ValueError as e:
            raise ApiError(400, "invalid_profile", str(e)[:300]) from None
        con = conn()
        with db.tx(con):
            con.execute("INSERT INTO app_style (user_id, profile, received_at) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE "
                        "SET profile=excluded.profile, received_at=excluded.received_at", (u["id"], dumps(profile), db.now()))
        return no_store({"ok": True})

    @app.get("/api/me/dashboard")
    def app_dashboard(request: Request):
        """-> Dashboard = {totals:{sessions, swings, active_min, rallies},
        weekly:[{week:"YYYY-Www", sessions, swings, active_min}] (12, oldest first),
        trends:[{id, started_at, p90_kmh, swings_per_min, median_gap_s, hr_avg}] (pod and pod+watch sessions, last 50, oldest first),
        wrist:{sessions, swings} (sensor "watch"),
        bests:{max_kmh, most_swings, longest_rally, best_rate} each {value, session_id, started_at} | null (max_kmh: never wrist),
        strokes:{groups:{power, mid, touch, not_a_hit, unclassified} shares over the last 10 sessions with strokes,
                 overall:{same keys} shares over all sessions with strokes, sessions:int (of the last 10), experimental:true}
                 | null (no sessions with strokes, or no strokes counted); shares are rounded to 4 decimals and sum to 1,
        style: StyleProfile | null, twin_model_id: int | null}
        A session without a report counts with its flat numbers (duration as active time, swings, best_kmh, avg_bpm)."""
        u = require_user(request, csrf=False)
        con = conn()
        # ponytail: parses every stored report per request (<= 5,000 x 256 KB); cache per user if the dashboard gets slow
        rows = [{**dict(x), "report": stored_report(x)} for x in con.execute("SELECT * FROM play_sessions WHERE user_id=?", (u["id"],))]
        st = con.execute("SELECT profile FROM app_style WHERE user_id=?", (u["id"],)).fetchone()
        twin = con.execute("SELECT id FROM models WHERE user_id=? AND status='active' AND style_profile IS NOT NULL "
                           "ORDER BY id DESC LIMIT 1", (u["id"],)).fetchone()
        return no_store(dashboard.aggregate(rows, json.loads(st["profile"]) if st else None, twin["id"] if twin else None,
                                            db.parse_t(db.now()).date()))

    @app.post("/auth/logout")
    def logout(request: Request):
        require_user(request)
        con = conn()
        con.execute("DELETE FROM sessions WHERE hash=?", (auth.hash_sid(request.cookies.get(auth.SESSION_COOKIE, "")),))
        resp = Response(status_code=204)
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    # ---- GitHub OAuth
    @app.get("/auth/github/login")
    def gh_login(request: Request):
        if not cfg.github_client_id:
            return RedirectResponse("/", status_code=302)
        app_ch = request.query_params.get("app")
        if app_ch is not None and not auth.valid_challenge(app_ch):
            return RedirectResponse(f"{auth.APP_SCHEME}://auth?error=bad_request", status_code=302)
        url, cookie = auth.oauth_start(request.query_params.get("next"), app_ch)
        resp = RedirectResponse(url, status_code=302)
        set_cookie(resp, auth.OAUTH_COOKIE, cookie, 600, path="/auth/github")
        resp.headers["cache-control"] = "no-store"
        return resp

    @app.get("/auth/github/callback")
    async def gh_callback(request: Request):
        raw = request.cookies.get(auth.OAUTH_COOKIE, "")
        st = auth.oauth_cookie_state(raw)
        app_flow = bool(st and st[2])

        def fail(reason):
            reason = re.sub(r"[^a-z0-9_]", "", reason.lower())[:40] or "unknown"
            # Every failure says why (a short code, never a secret), in the log and to the site or the app, which show a message.
            print(f"github sign-in failed{' (app)' if app_flow else ''}: {reason}", flush=True)
            to = f"{auth.APP_SCHEME}://auth?error={reason}" if app_flow else f"/arena?signin=failed&reason={reason}"
            r = RedirectResponse(to, status_code=302)
            r.delete_cookie(auth.OAUTH_COOKIE, path="/auth/github")
            r.headers["cache-control"] = "no-store"
            return r

        if request.query_params.get("error"):  # the user pressed Cancel on GitHub, or the app is suspended
            return fail("denied" if request.query_params.get("error") == "access_denied" else "github_error")
        state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
        if not raw:
            return fail("no_state_cookie")
        if not st:
            return fail("state_expired")
        if not state or not code or len(code) > 200 or not auth.csrf_ok(st[0], state):
            return fail("state_mismatch")
        try:
            gh = await auth.github_user(code)
        except auth.SignInError as e:
            return fail(e.code)
        except Exception as e:  # noqa: BLE001
            return fail(f"error_{type(e).__name__}"[:40])

        def login():
            """-> the session id (web) or the app's one-time code (app flow); None when banned."""
            con = conn()
            with db.tx(con):
                if con.execute("SELECT 1 FROM bans WHERE github_id=?", (gh["id"],)).fetchone():
                    return None
                con.execute("INSERT INTO users (id, login, avatar_url, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE "
                            "SET login=excluded.login, avatar_url=excluded.avatar_url", (gh["id"], gh["login"], gh["avatar_url"], db.now()))
                if app_flow:
                    one = secrets.token_urlsafe(32)
                    exp = db.fmt_t(db.parse_t(db.now()) + __import__("datetime").timedelta(seconds=auth.APP_CODE_TTL_S))
                    con.execute("DELETE FROM app_codes WHERE expires_at<?", (db.now(),))
                    con.execute("INSERT INTO app_codes (hash, user_id, challenge, expires_at) VALUES (?, ?, ?, ?)",
                                (auth.hash_sid(one), gh["id"], st[2], exp))
                    return one
                old = request.cookies.get(auth.SESSION_COOKIE)
                if old:
                    con.execute("DELETE FROM sessions WHERE hash=?", (auth.hash_sid(old),))
                return auth.create_session(con, gh["id"])
        out = await asyncio.to_thread(login)
        if not out:
            return fail("banned")
        if app_flow:
            resp = RedirectResponse(f"{auth.APP_SCHEME}://auth?code={out}", status_code=302)
        else:
            resp = RedirectResponse(st[1], status_code=302)
            set_cookie(resp, auth.SESSION_COOKIE, out, auth.SESSION_DAYS * 86400)
        resp.delete_cookie(auth.OAUTH_COOKIE, path="/auth/github")
        resp.headers["cache-control"] = "no-store"
        return resp

    @app.post("/api/app/session")
    async def app_session(request: Request):
        """The iPhone app trades its one-time code + PKCE verifier for a session id (sent back as the oso_session cookie value)."""
        try:
            body = await request.json()
            code, verifier = body.get("code"), body.get("verifier")
        except Exception:  # noqa: BLE001
            code = verifier = None
        if not isinstance(code, str) or not isinstance(verifier, str) or not (43 <= len(verifier) <= 128) or len(code) > 100:
            raise ApiError(400, "bad_request", "code and verifier are required.")

        def take():
            con = conn()
            with db.tx(con):
                r = con.execute("SELECT * FROM app_codes WHERE hash=?", (auth.hash_sid(code),)).fetchone()
                if r:
                    con.execute("DELETE FROM app_codes WHERE hash=?", (r["hash"],))  # one use, right or wrong
                if not r or r["expires_at"] < db.now() or not hmac.compare_digest(r["challenge"], auth.challenge_of(verifier)):
                    return None, None
                u = con.execute("SELECT id, login, avatar_url FROM users WHERE id=?", (r["user_id"],)).fetchone()
                con.execute("UPDATE users SET app_linked_at=? WHERE id=? AND app_linked_at IS NULL", (db.now(), r["user_id"]))
                return auth.create_session(con, r["user_id"]), dict(u)
        sid, user = await asyncio.to_thread(take)
        if not sid:
            raise ApiError(400, "invalid_code", "That sign-in code is expired, used or wrong. Sign in again.")
        return JSONResponse({"session": sid, "cookie": auth.SESSION_COOKIE, "user": user, "expires_in_days": auth.SESSION_DAYS},
                            headers={"cache-control": "no-store"})

    # ---- static: the viewer, the site, history routing
    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    def static(path: str, request: Request):
        p = "/" + path
        if p.startswith("/api/") or p == "/api" or p.startswith("/auth/") or p == "/healthz":
            raise ApiError(404, "not_found", "Not found.")
        if p == "/viewer":
            return RedirectResponse("/viewer/", status_code=301)
        if p.startswith("/viewer/"):
            rel = p[len("/viewer/"):] or "index.html"
            r = viewer.respond(request, rel, "no-cache", CSP_VIEWER)
            return r or Response("Not found\n", status_code=404, media_type="text/plain", headers={"content-security-policy": CSP_VIEWER})
        if p.startswith("/assets/"):
            r = site.respond(request, p, "public, max-age=31536000, immutable", CSP)
            return r or Response("Not found\n", status_code=404, media_type="text/plain")
        if p != "/" and site.get(p) and p != "/index.html":
            return site.respond(request, p, "public, max-age=3600", CSP)
        return site.respond(request, "index.html", "no-cache", CSP) or Response("site missing", status_code=500)

    return app


def health_app():
    from oso_arena import KIT_VERSION
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def healthz():
        out = {"kit_version": KIT_VERSION}
        try:
            con = conn()
            out["db"] = "ok" if con.execute("SELECT 1").fetchone() else "error"
            out["queue"] = {r["kind"]: r["c"] for r in con.execute("SELECT kind, COUNT(*) c FROM jobs WHERE status='queued' GROUP BY kind")}
            out["running"] = [{"worker": r["worker"], "kind": r["kind"], "since": r["started_at"]}
                              for r in con.execute("SELECT worker, kind, started_at FROM jobs WHERE status='running'")]
            out["workers"] = {r["k"][len("worker:"):]: r["v"] for r in con.execute("SELECT k, v FROM meta WHERE k LIKE 'worker:%'")}
            out["replay_cache_bytes"] = con.execute("SELECT COALESCE(SUM(size),0) s FROM replays WHERE status='ready'").fetchone()["s"]
            out["season"] = season_json(db.active_season(con))
        except Exception as e:  # noqa: BLE001
            out["db"] = f"error: {type(e).__name__}"
        return JSONResponse(out, status_code=200 if out.get("db") == "ok" else 503)
    return app


def main():
    import contextlib

    import uvicorn
    cfg = config.get()
    common = dict(proxy_headers=False, server_header=False, access_log=False, timeout_keep_alive=5, limit_concurrency=256,
                  log_level="info", lifespan="off")
    main_srv = uvicorn.Server(uvicorn.Config(create_app(), host=cfg.host, port=cfg.port, **common))

    class Quiet(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):
            yield

    health = Quiet(uvicorn.Config(health_app(), host="127.0.0.1", port=cfg.health_port, **common))

    async def run():
        ht = asyncio.create_task(health.serve())
        await main_srv.serve()
        health.should_exit = True
        await ht
    asyncio.run(run())


if __name__ == "__main__":
    main()
