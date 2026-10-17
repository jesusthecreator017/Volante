"""Acceptance 11.1: every smoke file through the real upload pipeline (HTTP -> web -> supervisor -> sandboxed child), compared
with smoke/expected.json. Usage: python tests/smoke_pipeline.py <base url> <smoke dir>   (env: the server's OSO_ARENA_* settings,
used only to mint test sessions directly in the DB: no GitHub needed)."""
import json
import sys
import time
from pathlib import Path

import httpx

from osoarena import auth, db


def session_for(gh_id, login):
    con = db.connect()
    with db.tx(con):
        con.execute("INSERT OR IGNORE INTO users (id, login, avatar_url, created_at) VALUES (?, ?, ?, ?)",
                    (gh_id, login, f"https://avatars.githubusercontent.com/u/{gh_id}?s=64", db.now()))
        sid = auth.create_session(con, gh_id)
    return sid


def client(base, gh_id, login):
    c = httpx.Client(base_url=base, cookies={auth.SESSION_COOKIE: session_for(gh_id, login)}, timeout=60)
    c.headers["x-csrf-token"] = c.get("/api/me").json()["csrf"]
    return c


def main(base, smoke):
    smoke = Path(smoke)
    expected = json.loads((smoke / "expected.json").read_text())
    fails = 0
    for i, (name, exp) in enumerate(sorted(expected.items())):
        c = client(base, 900000 + i, f"smoke-user-{i}")  # one test user per file: one active model each, 5/day limit not hit
        t0 = time.time()
        r = c.post("/api/models", files={"file": (name, (smoke / name).read_bytes(), "application/octet-stream")})
        if r.status_code == 413:
            got = {"valid": False, "http": 413, "detail": r.json().get("detail")}
            ok = not exp["valid"] and "oversize" in name
            print(f"{'PASS' if ok else 'FAIL'} {name}: HTTP 413 {r.json()}")
            fails += not ok
            continue
        assert r.status_code == 202, (name, r.status_code, r.text)
        mid = r.json()["id"]
        while True:
            m = c.get(f"/api/models/{mid}").json()
            if m["status"] != "pending":
                break
            time.sleep(0.5)
        dt = time.time() - t0
        if exp["valid"]:
            ok = m["status"] == "active" and m["rating"] == 1200
        else:
            ok = m["status"] == "rejected" and m.get("reject_reasons") == exp["reasons"] and m["rating"] is None
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} {name}: {m['status']} {m.get('reject_reasons', '')} name={m['name']!r} "
              f"build={m['build']} ({dt:.1f}s)")
    print(f"{len(expected) - fails}/{len(expected)} smoke files as expected")
    return fails


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1], sys.argv[2]) else 0)
