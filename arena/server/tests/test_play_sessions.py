"""Play sessions: the app uploads each finished session to the signed-in GitHub account (PUT /api/me/sessions/{id}, a flat body
with an optional volante-report/1); the site lists them and the dashboard aggregates them. Also /api/me/style and app_linked.
In-process, on a throwaway data dir.

Run:  python -m pytest -q tests/test_play_sessions.py
"""
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

os.environ["OSO_ARENA_DATA_DIR"] = tempfile.mkdtemp(prefix="arena-play-")
os.environ.setdefault("OSO_ARENA_SESSION_SECRET", "test-secret-" + "x" * 40)
os.environ["OSO_ARENA_COOKIE_SECURE"] = "0"
os.environ["OSO_ARENA_GITHUB_CLIENT_ID"] = "Ov23testclientid0000"
os.environ["OSO_ARENA_GITHUB_CLIENT_SECRET"] = "t" * 40
os.environ["OSO_ARENA_BASE_URL"] = "http://testserver"
os.environ["OSO_ARENA_RATE_WRITES_PER_MIN"] = "10000"
os.environ["OSO_ARENA_RATE_READS_PER_MIN"] = "10000"
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from osoarena import auth, db, web  # noqa: E402
from test_dashboard import fixture, rep  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PROFILE = json.loads((ROOT / "arena/kit/tests/data/twins_vector.profile.json").read_text())
_ids = iter(range(700001, 799999))


@pytest.fixture(scope="module")
def app():
    return web.create_app()


def signed_in(c, login="u"):
    gh_id = next(_ids)
    con = db.connect()
    with db.tx(con):
        con.execute("INSERT OR IGNORE INTO users (id, login, avatar_url, created_at) VALUES (?, ?, '', ?)", (gh_id, login, db.now()))
        sid = auth.create_session(con, gh_id)
    c.cookies.set(auth.SESSION_COOKIE, sid)
    c.headers["x-csrf-token"] = c.get("/api/me").json()["csrf"]
    c.uid = gh_id
    return c


def body(rp, sensor=None, **kw):
    """The flat PUT body the app sends for `rp` (flat numbers + the report)."""
    s = rp["session"]
    return {**{"started_at": s["started_at"], "sensor": sensor or s["source"], "duration_s": s["duration_s"],
            "swings": rp["swings"]["count"], "hits": 0, "best_kmh": rp["speed"]["max_kmh"],
            "avg_bpm": rp["heart_rate"] and rp["heart_rate"]["avg_bpm"], "report": rp}, **kw}


def rows(uid, table="play_sessions"):
    return db.connect().execute(f"SELECT * FROM {table} WHERE user_id=?", (uid,)).fetchall()


def test_put_get_list_delete_roundtrip(app):
    with TestClient(app) as c:
        signed_in(c)
        for r in fixture():
            res = c.put(f"/api/me/sessions/{r['id']}", json=body(r["report"]))
            assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
            out = res.json()
            assert out["id"] == r["id"] and out["started_at"] == r["report"]["session"]["started_at"] and "report" not in out
        g = c.get("/api/me/sessions/p2")
        assert g.status_code == 200 and g.headers["cache-control"] == "no-store"
        assert g.json()["id"] == "p2" and g.json()["report"] == fixture()[1]["report"] and g.json()["sensor"] == "pod"
        page = c.get("/api/me/sessions?limit=3").json()
        assert [i["id"] for i in page["items"]] == ["p5", "w2", "p4"] and page["next_before"] == "2026-10-12T18:00:00Z~p4"
        item = page["items"][0]
        assert item == {"id": "p5", "started_at": "2026-10-17T09:30:00Z", "sensor": "pod", "duration_s": 960.0, "swings": 150,
                        "hits": 0, "best_kmh": 91.0, "avg_bpm": 150, "uploaded_at": item["uploaded_at"]}
        page = c.get(f"/api/me/sessions?limit=3&before={page['next_before']}").json()
        assert [i["id"] for i in page["items"]] == ["p3", "w1", "p2"]
        page = c.get(f"/api/me/sessions?before={page['next_before']}").json()
        assert [i["id"] for i in page["items"]] == ["p1"] and page["next_before"] is None
        assert len(c.get("/api/me/sessions").json()["items"]) == 7
        for q in ("limit=0", "limit=501", "limit=x", "before=yesterday", "before=2026-10-12T18:00:00Z%0A"):
            assert c.get(f"/api/me/sessions?{q}").status_code == 400, q
        assert c.delete("/api/me/sessions/p2").status_code == 204
        assert c.get("/api/me/sessions/p2").status_code == 404
        assert c.delete("/api/me/sessions/p2").status_code == 404
        d = c.get("/api/me/dashboard")
        assert d.status_code == 200 and d.headers["cache-control"] == "no-store"
        assert d.json()["totals"]["sessions"] == 6 and d.json()["style"] is None and d.json()["twin_model_id"] is None
    with TestClient(app) as anon:
        for m, p in (("get", "/api/me/sessions"), ("get", "/api/me/sessions/p1"), ("get", "/api/me/dashboard"),
                     ("delete", "/api/me/sessions"), ("put", "/api/me/style")):
            assert getattr(anon, m)(p).status_code == 401


def test_cursor_keeps_same_second_sessions(app):
    with TestClient(app) as c:
        signed_in(c)
        for sid in ("x1", "x2", "x3"):
            assert c.put(f"/api/me/sessions/{sid}", json=body(rep("2026-10-01T18:00:00Z"))).status_code == 200
        assert c.put("/api/me/sessions/old", json=body(rep("2026-09-01T18:00:00Z"))).status_code == 200
        seen, before = [], None
        while True:
            q = "limit=1" + (f"&before={before}" if before else "")
            page = c.get(f"/api/me/sessions?{q}").json()
            seen += [i["id"] for i in page["items"]]
            before = page["next_before"]
            if before is None:
                break
        assert seen == ["x3", "x2", "x1", "old"]
        assert [i["id"] for i in c.get("/api/me/sessions?before=2026-10-01T18:00:00Z~x2").json()["items"]] == ["x1", "old"]
        assert [i["id"] for i in c.get("/api/me/sessions?before=2026-10-01T18:00:01Z").json()["items"]] == ["x3", "x2", "x1", "old"]
        for bad in ("2026-10-01T18:00:00Z~", "2026-10-01T18:00:00Z~a b", "x~x1", "2026-10-01T18:00:00Z~x1~x2"):
            assert c.get(f"/api/me/sessions?before={bad}").status_code == 400, bad


def test_put_is_idempotent_by_id(app):
    with TestClient(app) as c:
        signed_in(c)
        r = rep("2026-10-01T18:00:00Z")
        assert c.put("/api/me/sessions/a.b_c-1", json=body(r)).status_code == 200
        assert c.put("/api/me/sessions/a.b_c-1", json=body(r)).status_code == 200
        r2 = rep("2026-10-02T18:00:00Z", swings=99)
        assert c.put("/api/me/sessions/a.b_c-1", json=body(r2)).status_code == 200
        got = rows(c.uid)
        assert len(got) == 1 and got[0]["started_at"] == "2026-10-02T18:00:00Z" and json.loads(got[0]["report"]) == r2


def test_put_rejects_invalid_and_oversize(app):
    with TestClient(app) as c:
        signed_in(c)
        good = rep("2026-10-01T18:00:00Z")
        bad = json.loads(json.dumps(good))
        bad["swings"]["count"] = -1
        for b in (body(bad), body(good, report={"schema": "volante-report/1", "summary": {}})):
            res = c.put("/api/me/sessions/x", json=b)
            assert res.status_code == 400 and res.json()["error"] == "invalid_report", b
        for b in ({"report": good}, {}, [], body(good, started_at="2026-02-30T10:00:00Z"), body(good, duration_s=float("inf"))):
            res = c.put("/api/me/sessions/x", content=json.dumps(b).encode(), headers={"content-type": "application/json"})
            assert res.status_code == 400 and res.json()["error"] == "invalid_session", b
        for raw in (b"{", b"", json.dumps(body(good)).replace("60.0", "NaN").encode()):
            res = c.put("/api/me/sessions/x", content=raw, headers={"content-type": "application/json"})
            assert res.status_code == 400, raw
        big = json.dumps(body(good)).encode() + b" " * (web.MAX_REPORT + 4096)
        res = c.put("/api/me/sessions/x", content=big, headers={"content-type": "application/json"})
        assert res.status_code == 413 and res.json()["error"] == "too_large"
        res = c.put("/api/me/sessions/x", content=b"{}", headers={"content-type": "application/json", "content-length": str(web.MAX_REPORT + 5000)})
        assert res.status_code == 413  # a declared Content-Length over the cap is refused before reading
        assert rows(c.uid) == []


def test_bad_session_id_400(app):
    with TestClient(app) as c:
        signed_in(c)
        r = body(rep("2026-10-01T18:00:00Z"))
        for bad in ("a" * 65, "a%20b", "a$b", "%C3%A9t%C3%A9", "a%0A", "%2E", "%2E%2E", ".a", "-a", "_a"):
            assert c.put(f"/api/me/sessions/{bad}", json=r).status_code == 400, bad
            assert c.get(f"/api/me/sessions/{bad}").status_code == 400, bad
            assert c.delete(f"/api/me/sessions/{bad}").status_code == 400, bad
        assert c.put("/api/me/sessions/" + "a" * 64, json=r).status_code == 200
        assert rows(c.uid)[0]["id"] == "a" * 64


def test_sessions_are_private_to_the_owner(app):
    with TestClient(app) as a, TestClient(app) as b:
        signed_in(a, "alice")
        signed_in(b, "bob")
        ra = rep("2026-10-01T18:00:00Z", swings=11)
        assert a.put("/api/me/sessions/same", json=body(ra)).status_code == 200
        assert b.get("/api/me/sessions/same").status_code == 404
        assert b.delete("/api/me/sessions/same").status_code == 404
        assert b.get("/api/me/sessions").json() == {"items": [], "next_before": None}
        assert b.get("/api/me/dashboard").json()["totals"]["sessions"] == 0
        # B's PUT of the same id writes B's own row only (ids are per user); A's row is untouched
        assert b.put("/api/me/sessions/same", json=body(rep("2026-10-02T18:00:00Z", swings=22))).status_code == 200
        assert b.delete("/api/me/sessions").status_code == 204
        assert len(rows(a.uid)) == 1 and json.loads(rows(a.uid)[0]["report"]) == ra
        assert a.get("/api/me/sessions/same").json()["report"] == ra


def test_writes_need_csrf(app):
    with TestClient(app) as c:
        signed_in(c)
        h = {"x-csrf-token": "wrong"}
        r = body(rep("2026-10-01T18:00:00Z"))
        assert c.put("/api/me/sessions/x", json=r, headers=h).status_code == 403
        assert c.put("/api/me/style", json=PROFILE, headers=h).status_code == 403
        assert c.put("/api/me/sessions/x", json=r).status_code == 200
        assert c.delete("/api/me/sessions/x", headers=h).status_code == 403
        assert c.delete("/api/me/sessions", headers=h).status_code == 403
        assert len(rows(c.uid)) == 1


def test_delete_all_removes_sessions_and_style(app):
    with TestClient(app) as c:
        signed_in(c)
        for r in fixture()[:3]:
            c.put(f"/api/me/sessions/{r['id']}", json=body(r["report"]))
        assert c.put("/api/me/style", json=PROFILE).status_code == 200
        assert c.get("/api/me/dashboard").json()["style"] == PROFILE
        assert c.delete("/api/me/sessions").status_code == 204
        assert rows(c.uid) == [] and rows(c.uid, "app_style") == []
        assert c.get("/api/me/dashboard").json()["style"] is None


def test_delete_account_removes_app_rows(app):
    with TestClient(app) as c:
        signed_in(c)
        con = db.connect()
        with db.tx(con):
            con.execute("INSERT INTO play_sessions VALUES ('x', ?, '2026-10-01T18:00:00Z', 'pod', 60, 1, 0, NULL, NULL, NULL, ?)",
                        (c.uid, db.now()))
            con.execute("INSERT INTO app_style VALUES (?, '{}', ?)", (c.uid, db.now()))
        assert len(rows(c.uid)) == 1 and len(rows(c.uid, "app_style")) == 1
        assert c.delete("/api/me").status_code == 204
        assert rows(c.uid) == [] and rows(c.uid, "app_style") == []
        assert db.connect().execute("SELECT 1 FROM users WHERE id=?", (c.uid,)).fetchone() is None


def test_style_put_validates(app):
    with TestClient(app) as c:
        signed_in(c)
        for bad in ({"schema": "volante-style/1"}, dict(PROFILE, extra=1), dict(PROFILE, speed=None), [], "x"):
            res = c.put("/api/me/style", json=bad)
            assert res.status_code == 400 and res.json()["error"] == "invalid_profile", bad
        res = c.put("/api/me/style", content=b"{", headers={"content-type": "application/json"})
        assert res.status_code == 400
        res = c.put("/api/me/style", content=json.dumps(dict(PROFILE, notes="x" * 20000)).encode(), headers={"content-type": "application/json"})
        assert res.status_code in (400, 413)
        assert rows(c.uid, "app_style") == []
        assert c.put("/api/me/style", json=PROFILE).status_code == 200
        assert json.loads(rows(c.uid, "app_style")[0]["profile"]) == PROFILE


def test_rate_limit_put(app):
    with TestClient(app) as c:
        signed_in(c)
        r = body(rep("2026-10-01T18:00:00Z"))
        for i in range(60):
            assert c.put(f"/api/me/sessions/s{i}", json=r).status_code == 200, i
        res = c.put("/api/me/sessions/s60", json=r)
        assert res.status_code == 429 and res.json()["error"] == "rate_limited" and int(res.headers["retry-after"]) >= 1
        assert c.put("/api/me/style", json=PROFILE).status_code == 429
        assert len(rows(c.uid)) == 60
    with TestClient(app) as other:  # per user
        signed_in(other)
        assert other.put("/api/me/sessions/s0", json=r).status_code == 200


def test_session_cap(app, monkeypatch):
    monkeypatch.setattr(web, "MAX_APP_SESSIONS", 2)
    with TestClient(app) as c:
        signed_in(c)
        r = body(rep("2026-10-01T18:00:00Z"))
        assert c.put("/api/me/sessions/a", json=r).status_code == 200
        assert c.put("/api/me/sessions/b", json=r).status_code == 200
        res = c.put("/api/me/sessions/c", json=r)
        assert res.status_code == 400 and res.json()["error"] == "too_many_sessions"
        assert c.put("/api/me/sessions/a", json=r).status_code == 200  # updating an existing id is fine


def test_dashboard_twin_model(app):
    with TestClient(app) as c:
        signed_in(c)
        con = db.connect()
        with db.tx(con):
            sid = con.execute("SELECT id FROM seasons WHERE active=1").fetchone()[0]
            con.execute("INSERT INTO models (season_id, user_id, uploaded_by, name, build, status, created_at, style_profile) "
                        "VALUES (?, ?, ?, 'twin', ?, 'active', ?, ?)",
                        (sid, c.uid, c.uid, json.dumps(db.PLACEHOLDER_BUILD), db.now(), json.dumps(PROFILE)))
            mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        assert c.get("/api/me/dashboard").json()["twin_model_id"] == mid


def test_app_exchange_sets_app_linked(app, monkeypatch):
    async def ok(code):
        return {"id": 5151, "login": "phone2", "avatar_url": ""}

    monkeypatch.setattr(auth, "github_user", ok)
    with TestClient(app) as c:
        con = db.connect()
        with db.tx(con):
            con.execute("INSERT INTO users (id, login, avatar_url, created_at) VALUES (5151, 'phone2', '', ?)", (db.now(),))
            sid = auth.create_session(con, 5151)
        web_me = c.get("/api/me", headers={"cookie": f"{auth.SESSION_COOKIE}={sid}"}).json()
        assert web_me["app_linked"] is False
        verifier = secrets.token_urlsafe(48)
        r = c.get(f"/auth/github/login?app={auth.challenge_of(verifier)}", follow_redirects=False)
        st = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        loc = c.get(f"/auth/github/callback?code=x&state={st}", follow_redirects=False).headers["location"]
        code = parse_qs(urlsplit(loc).query)["code"][0]
        assert c.post("/api/app/session", json={"code": code, "verifier": verifier}).status_code == 200
        first = db.connect().execute("SELECT app_linked_at FROM users WHERE id=5151").fetchone()[0]
        assert first
        assert c.get("/api/me", headers={"cookie": f"{auth.SESSION_COOKIE}={sid}"}).json()["app_linked"] is True


# ---------------------------------------------------------------- main's contract (PR #78): the flat body phones already send

FLAT = {"started_at": "2026-10-18T02:50:30Z", "sensor": "pod+watch", "duration_s": 312.5, "swings": 120, "hits": 64,
        "best_kmh": 151.0, "avg_bpm": 142.0, "report": rep("2026-10-18T02:50:30Z", swings=120)}


def test_play_sessions_flat_body(app):
    with TestClient(app) as anon:
        assert anon.get("/api/me/sessions").status_code == 401
    with TestClient(app) as a, TestClient(app) as b:
        signed_in(a, "alice")
        signed_in(b, "bob")
        r = a.put("/api/me/sessions/20261018-025030-c2d6", json=FLAT)
        assert r.status_code == 200 and r.json()["swings"] == 120
        assert a.put("/api/me/sessions/20261018-025030-c2d6", json={**FLAT, "swings": 121}).json()["swings"] == 121  # idempotent
        items = a.get("/api/me/sessions").json()["items"]
        assert [i["id"] for i in items] == ["20261018-025030-c2d6"] and "report" not in items[0]
        assert a.get("/api/me/sessions/20261018-025030-c2d6").json()["report"]["schema"] == "volante-report/1"
        # a report is optional; fractional seconds are accepted and stored as whole seconds
        no_rep = {k: v for k, v in FLAT.items() if k != "report"}
        assert a.put("/api/me/sessions/20261018-025031", json={**no_rep, "started_at": "2026-10-18T02:50:31.250Z"}).status_code == 200
        got = a.get("/api/me/sessions/20261018-025031").json()
        assert got["report"] is None and got["started_at"] == "2026-10-18T02:50:31Z"
        # owner only
        assert b.get("/api/me/sessions").json() == {"items": [], "next_before": None}
        assert b.get("/api/me/sessions/20261018-025030-c2d6").status_code == 404
        # validation and CSRF
        for bad in ({**FLAT, "sensor": "phone"}, {**FLAT, "swings": -1}, {**FLAT, "started_at": "yesterday"},
                    {**FLAT, "report": {"schema": "other"}}, {**FLAT, "avg_bpm": 999}):
            assert a.put("/api/me/sessions/20261018-025030-c2d6", json=bad).status_code == 400
        assert all(a.put(f"/api/me/sessions/{x}", json=FLAT).status_code == 400 for x in (".hidden", "a b", "x" * 65))
        assert a.put("/api/me/sessions/20261018-025030-c2d6", json=FLAT, headers={"x-csrf-token": "wrong"}).status_code == 403
        assert a.delete("/api/me/sessions/20261018-025030-c2d6").status_code == 204
        assert [i["id"] for i in a.get("/api/me/sessions").json()["items"]] == ["20261018-025031"]
        a.put("/api/me/sessions/20261018-025030-c2d6", json=FLAT)
        assert a.delete("/api/me").status_code == 204
        assert not rows(a.uid)


def test_first_put_sets_app_linked(app):
    """An app build that signed in before the exchange marked app_linked: its first upload does."""
    with TestClient(app) as c:
        signed_in(c)
        assert c.get("/api/me").json()["app_linked"] is False
        assert c.put("/api/me/sessions/20261018-025030", json=FLAT).status_code == 200
        assert c.get("/api/me").json()["app_linked"] is True


def test_main_session_ids_match_the_id_rule():
    """main's app ids (yyyyMMdd-HHmm[ss][-xxxx]) are a subset of the server's rule."""
    for sid in ("20261018-0250", "20261018-025030", "20261018-025030-c2d6"):
        assert web.APP_SESSION_ID.fullmatch(sid)


def test_rows_stored_before_the_validator_fall_back_to_flat_numbers(app):
    """Rows main's server stored with a stub report: GET shows no report and the dashboard uses the flat columns."""
    with TestClient(app) as c:
        signed_in(c)
        con = db.connect()
        with db.tx(con):
            con.execute("INSERT INTO play_sessions VALUES ('20261018-0250', ?, '2026-10-18T02:50:00Z', 'pod', 600, 77, 5, 140, NULL, ?, ?)",
                        (c.uid, '{"schema":"volante-report/1","summary":{}}', db.now()))
        assert c.get("/api/me").json()["app_linked"] is True  # has sessions, though app_linked_at was never set
        assert c.get("/api/me/sessions/20261018-0250").json()["report"] is None
        d = c.get("/api/me/dashboard").json()
        assert d["totals"]["swings"] == 77 and d["bests"]["max_kmh"]["value"] == 140
