"""web (uvicorn + FastAPI): the API (PROMPT.md section 7), auth, uploads, replay downloads and the static site/viewer (section 8).
Never runs model code or the simulator. Listens on 127.0.0.1:8700 (Tailscale Funnel's target) and serves /healthz only on a
second loopback listener, 127.0.0.1:8701, that Funnel never points at."""
import asyncio
import gzip
import hashlib
import json
import mimetypes
import os
import secrets
import shutil
import threading
import time
import zlib
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartParser

from osoarena import auth, config, db, ladder, replays
from osoarena.text import dumps

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://avatars.githubusercontent.com; "
       "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
CSP_VIEWER = CSP.replace("script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'")
PAGE_PREFIXES = ("/", "/ladder", "/models/", "/matches/", "/tournaments", "/my-models", "/get-started", "/terms", "/privacy")
MULTIPART_OVERHEAD = 16 * 1024
TYPES = {".wasm": "application/wasm", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".json": "application/json",
         ".svg": "image/svg+xml", ".woff2": "font/woff2", ".gltf": "model/gltf+json", ".bin": "application/octet-stream",
         ".txt": "text/plain; charset=utf-8", ".png": "image/png"}
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


def model_summary(con, m):
    rating = None if m["status"] in ("pending", "rejected") or m["rating"] is None else round(m["rating"], 1)
    return {"id": m["id"], "name": m["name"], "owner": owner_of(con, m), "build": ladder.build_of(m), "status": m["status"],
            "rating": rating, "games": m["games"]}


def model_detail(con, m):
    d = model_summary(con, m)
    hist = con.execute("SELECT t, rating, match_id FROM (SELECT rowid AS rid, * FROM rating_history WHERE model_id=? "
                       "ORDER BY rowid DESC LIMIT 200) ORDER BY rid ASC", (m["id"],)).fetchall()
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
            "finished_at": mt["finished_at"], "status": mt["status"], "a": model_summary(con, a), "b": model_summary(con, b),
            "games_won": json.loads(mt["games_won"]) if mt["games_won"] else [0, 0],
            "winner": mt["winner"] if done else None,
            "games": json.loads(mt["games"]) if mt["games"] else [],
            "rating_delta": json.loads(mt["rating_delta"]) if done and mt["rating_delta"] and mt["kind"] != "tournament" else None}


def tournament_json(con, t, full=True):
    d = {"id": t["id"], "season_id": t["season_id"], "starts_at": t["starts_at"], "status": t["status"]}
    if full:
        cache = {}

        def ms(mid):
            if mid is None:
                return None
            if mid not in cache:
                cache[mid] = model_summary(con, con.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone())
            return cache[mid]
        rounds = json.loads(t["rounds"]) if t["status"] != "scheduled" else []
        d["rounds"] = [[{"seed_a": tie["seed_a"], "seed_b": tie["seed_b"], "a": ms(tie["a"]), "b": ms(tie["b"]),
                         "match_id": tie["match_id"], "winner": tie["winner"]} for tie in r] for r in rounds]
    return d


# ---------------------------------------------------------------- app

def create_app():
    cfg = config.get()
    for d in (cfg.db_path.parent, cfg.models_dir, cfg.quarantine_dir, cfg.logs_dir, cfg.replays_dir, cfg.jobs_dir):
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
            used = len(uploads_used(conn(), u["id"]))
            body = {"user": {"id": u["id"], "login": u["login"], "avatar_url": u["avatar_url"]}, "csrf": csrf,
                    "uploads_left_today": max(0, cfg.daily_uploads - used)}
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
        total = con.execute("SELECT COUNT(*) c FROM models WHERE season_id=? AND status='active'", (season["id"],)).fetchone()["c"]
        rows = con.execute("SELECT * FROM models WHERE season_id=? AND status='active' ORDER BY rating DESC, id ASC LIMIT ? OFFSET ?",
                           (season["id"], limit, offset)).fetchall()
        items = [{"rank": offset + i + 1, "model": model_summary(con, m), "rating": round(m["rating"] or 1200, 1),
                  "provisional": m["games"] < cfg.provisional_games, "games": m["games"], "wins": m["wins"], "losses": m["losses"]}
                 for i, m in enumerate(rows)]
        return {"season": season_json(season), "total": total, "items": items}

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
    def t_current():
        con = conn()
        t = con.execute("SELECT * FROM tournaments WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone() or \
            con.execute("SELECT * FROM tournaments WHERE status='scheduled' ORDER BY starts_at ASC LIMIT 1").fetchone()
        if not t:
            raise ApiError(404, "not_found", "No tournament is scheduled yet.")
        return tournament_json(con, t)

    @app.get("/api/tournaments")
    def t_list(request: Request):
        con = conn()
        sid = int_param(request.query_params.get("season"), "season", None)
        sql, args = "SELECT * FROM tournaments WHERE status!='skipped'", []
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
            os.replace(tmp, dst)  # same filesystem; models/ is setgid oso-arena-sandbox, so the child can read it
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
        url, cookie = auth.oauth_start(request.query_params.get("next"))
        resp = RedirectResponse(url, status_code=302)
        set_cookie(resp, auth.OAUTH_COOKIE, cookie, 600, path="/auth/github")
        resp.headers["cache-control"] = "no-store"
        return resp

    @app.get("/auth/github/callback")
    async def gh_callback(request: Request):
        fail = RedirectResponse("/", status_code=302)
        fail.delete_cookie(auth.OAUTH_COOKIE, path="/auth/github")
        st = auth.oauth_cookie_state(request.cookies.get(auth.OAUTH_COOKIE, ""))
        state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
        if not st or not state or not code or len(code) > 200 or not auth.csrf_ok(st[0], state):
            return fail
        try:
            gh = await auth.github_user(code)
        except Exception as e:  # noqa: BLE001
            print(f"github sign-in failed: {type(e).__name__}", flush=True)
            gh = None
        if not gh:
            return fail

        def login():
            con = conn()
            with db.tx(con):
                if con.execute("SELECT 1 FROM bans WHERE github_id=?", (gh["id"],)).fetchone():
                    return None
                con.execute("INSERT INTO users (id, login, avatar_url, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE "
                            "SET login=excluded.login, avatar_url=excluded.avatar_url", (gh["id"], gh["login"], gh["avatar_url"], db.now()))
                old = request.cookies.get(auth.SESSION_COOKIE)
                if old:
                    con.execute("DELETE FROM sessions WHERE hash=?", (auth.hash_sid(old),))
                return auth.create_session(con, gh["id"])
        sid = await asyncio.to_thread(login)
        if not sid:
            return fail
        resp = RedirectResponse(st[1], status_code=302)
        resp.delete_cookie(auth.OAUTH_COOKIE, path="/auth/github")
        set_cookie(resp, auth.SESSION_COOKIE, sid, auth.SESSION_DAYS * 86400)
        resp.headers["cache-control"] = "no-store"
        return resp

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
