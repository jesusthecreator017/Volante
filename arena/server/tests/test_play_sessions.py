"""Play sessions: the app uploads each finished session to the signed-in GitHub account; the site lists them.

Run:  python -m pytest -q tests/test_play_sessions.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["OSO_ARENA_DATA_DIR"] = tempfile.mkdtemp(prefix="arena-play-")
os.environ.setdefault("OSO_ARENA_SESSION_SECRET", "test-secret-" + "x" * 40)
os.environ["OSO_ARENA_COOKIE_SECURE"] = "0"
os.environ["OSO_ARENA_RATE_WRITES_PER_MIN"] = "1000"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from osoarena import db, web  # noqa: E402
from test_pods import signed_in  # noqa: E402

BODY = {"started_at": "2026-10-18T02:50:30Z", "sensor": "pod+watch", "duration_s": 312.5, "swings": 120, "hits": 64,
        "best_kmh": 151.0, "avg_bpm": 142.0, "report": {"schema": "volante-report/1", "summary": {"swings": 120}}}


def test_play_sessions():
    app = web.create_app()
    with TestClient(app) as anon:
        assert anon.get("/api/me/sessions").status_code == 401
    with TestClient(app) as a, TestClient(app) as b:
        signed_in(a, 910001, "alice")
        signed_in(b, 910002, "bob")
        r = a.put("/api/me/sessions/20261018-025030-c2d6", json=BODY)
        assert r.status_code == 200 and r.json()["swings"] == 120
        assert a.put("/api/me/sessions/20261018-025030-c2d6", json={**BODY, "swings": 121}).json()["swings"] == 121  # idempotent
        items = a.get("/api/me/sessions").json()["items"]
        assert [i["id"] for i in items] == ["20261018-025030-c2d6"] and "report" not in items[0]
        assert a.get("/api/me/sessions/20261018-025030-c2d6").json()["report"]["schema"] == "volante-report/1"
        # owner only
        assert b.get("/api/me/sessions").json() == {"items": []}
        assert b.get("/api/me/sessions/20261018-025030-c2d6").status_code == 404
        # validation and CSRF
        for bad in ({**BODY, "sensor": "phone"}, {**BODY, "swings": -1}, {**BODY, "started_at": "yesterday"},
                    {**BODY, "report": {"schema": "other"}}, {**BODY, "avg_bpm": 999}):
            assert a.put("/api/me/sessions/20261018-025030-c2d6", json=bad).status_code == 400
        assert all(a.put(f"/api/me/sessions/{x}", json=BODY).status_code == 404 for x in ("not-an-id", "2026", "x" * 30))
        assert a.put("/api/me/sessions/20261018-025030-c2d6", json=BODY, headers={"x-csrf-token": "wrong"}).status_code == 403
        assert a.delete("/api/me/sessions/20261018-025030-c2d6").status_code == 204
        assert a.get("/api/me/sessions").json() == {"items": []}
        a.put("/api/me/sessions/20261018-025030-c2d6", json=BODY)
        assert a.delete("/api/me").status_code == 204
        assert db.connect().execute("SELECT COUNT(*) FROM play_sessions").fetchone()[0] == 0
