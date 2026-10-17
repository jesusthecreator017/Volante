"""Pods endpoints (site Settings): list and rename the signed-in user's pods. In-process, on a throwaway data dir.

Run:  python -m pytest -q tests/test_pods.py   (needs the deploy/requirements.lock packages)
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["OSO_ARENA_DATA_DIR"] = tempfile.mkdtemp(prefix="arena-pods-")
os.environ.setdefault("OSO_ARENA_SESSION_SECRET", "test-secret-" + "x" * 40)
os.environ["OSO_ARENA_COOKIE_SECURE"] = "0"
os.environ["OSO_ARENA_RATE_WRITES_PER_MIN"] = "1000"  # one test client makes many writes
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from osoarena import auth, db, web  # noqa: E402


def signed_in(c, gh_id, login):
    con = db.connect()
    with db.tx(con):
        con.execute("INSERT OR IGNORE INTO users (id, login, avatar_url, created_at) VALUES (?, ?, '', ?)", (gh_id, login, db.now()))
        sid = auth.create_session(con, gh_id)
    c.cookies.set(auth.SESSION_COOKIE, sid)
    c.headers["x-csrf-token"] = c.get("/api/me").json()["csrf"]
    return c


def add_pod(user_id, hw_id, name):
    con = db.connect()
    with db.tx(con):
        return con.execute("INSERT INTO pods (user_id, hw_id, name, created_at) VALUES (?, ?, ?, ?)",
                           (user_id, hw_id, name, db.now())).lastrowid


def test_pods():
    app = web.create_app()
    with TestClient(app) as anon:
        assert anon.get("/api/me/pods").status_code == 401
    with TestClient(app) as a, TestClient(app) as b:
        signed_in(a, 900001, "alice")
        signed_in(b, 900002, "bob")
        assert a.get("/api/me/pods").json() == {"items": []}
        pid = add_pod(900001, "OSO-4C99", "Alice's racquet")
        r = a.get("/api/me/pods")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert r.json()["items"] == [{"id": f"pod_{pid}", "hw_id": "OSO-4C99", "name": "Alice's racquet", "last_seen": None}]
        # rename: trimmed, 1-40 characters, no category C
        r = a.patch(f"/api/me/pods/pod_{pid}", json={"name": "  Match racquet  "})
        assert r.status_code == 200 and r.json()["name"] == "Match racquet"
        for bad in ("", "   ", "x" * 41, "bad\u0007name", "zero​width", None, 5):
            assert a.patch(f"/api/me/pods/pod_{pid}", json={"name": bad}).json() == {"error": "invalid_name", "detail": a.patch(f"/api/me/pods/pod_{pid}", json={"name": bad}).json()["detail"]}
        assert a.patch(f"/api/me/pods/pod_{pid}", json={"name": "x" * 40}).status_code == 200
        # another user's pod and unknown ids are 404; CSRF is required
        assert b.patch(f"/api/me/pods/pod_{pid}", json={"name": "mine"}).status_code == 404
        assert b.get("/api/me/pods").json() == {"items": []}
        for bad_id in ("pod_999999", "nope", "pod_", "pod_1x"):
            assert a.patch(f"/api/me/pods/{bad_id}", json={"name": "n"}).status_code == 404
        assert a.patch(f"/api/me/pods/pod_{pid}", json={"name": "n"}, headers={"x-csrf-token": "wrong"}).status_code == 403
        # deleting the account removes its pods
        assert a.delete("/api/me").status_code == 204
        assert db.connect().execute("SELECT COUNT(*) FROM pods").fetchone()[0] == 0
