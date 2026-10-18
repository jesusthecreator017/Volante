"""GitHub sign-in callback: every failure redirects to /arena?signin=failed&reason=<code>; success sets the session cookie.
In-process with GitHub stubbed (no network). Run:  python -m pytest -q tests/test_signin.py
"""
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

os.environ["OSO_ARENA_DATA_DIR"] = tempfile.mkdtemp(prefix="arena-signin-")
os.environ.setdefault("OSO_ARENA_SESSION_SECRET", "test-secret-" + "x" * 40)
os.environ["OSO_ARENA_COOKIE_SECURE"] = "0"
os.environ["OSO_ARENA_GITHUB_CLIENT_ID"] = "Ov23testclientid0000"
os.environ["OSO_ARENA_GITHUB_CLIENT_SECRET"] = "t" * 40
os.environ["OSO_ARENA_BASE_URL"] = "http://testserver"
os.environ["OSO_ARENA_RATE_READS_PER_MIN"] = "1000"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from osoarena import auth, web  # noqa: E402


def start(c, next_path="/arena/ladder"):
    r = c.get(f"/auth/github/login?next={next_path}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    return parse_qs(urlsplit(r.headers["location"]).query)["state"][0]


def reason(r):
    assert r.status_code == 302, r.status_code
    loc = urlsplit(r.headers["location"])
    assert loc.path == "/arena", loc
    q = parse_qs(loc.query)
    assert q["signin"] == ["failed"]
    return q["reason"][0]


def test_signin(monkeypatch):
    app = web.create_app()

    async def ok(code):
        return {"id": 4242, "login": "octo", "avatar_url": "https://avatars.githubusercontent.com/u/4242"}

    async def bad_creds(code):
        raise auth.SignInError("incorrect_client_credentials")

    with TestClient(app) as c:
        # no oauth cookie at all (e.g. blocked cookies, or a callback opened in another browser)
        assert reason(c.get("/auth/github/callback?code=x&state=y", follow_redirects=False)) == "no_state_cookie"
        # the user cancelled on GitHub
        start(c)
        assert reason(c.get("/auth/github/callback?error=access_denied", follow_redirects=False)) == "denied"
        # a state that isn't ours
        start(c)
        assert reason(c.get("/auth/github/callback?code=x&state=forged", follow_redirects=False)) == "state_mismatch"
        # GitHub refuses the stored client ID / secret
        monkeypatch.setattr(auth, "github_user", bad_creds)
        st = start(c)
        assert reason(c.get(f"/auth/github/callback?code=x&state={st}", follow_redirects=False)) == "incorrect_client_credentials"
        # success: back to `next`, signed in, /api/me knows the user
        monkeypatch.setattr(auth, "github_user", ok)
        st = start(c)
        r = c.get(f"/auth/github/callback?code=x&state={st}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/arena/ladder"
        assert auth.SESSION_COOKIE in r.cookies or auth.SESSION_COOKIE in c.cookies
        assert c.get("/api/me").json()["user"]["login"] == "octo"


def test_app_signin(monkeypatch):
    """The iPhone flow: login?app=<challenge> -> callback -> volante://auth?code= -> POST /api/app/session with the verifier."""
    import secrets as s_
    app = web.create_app()

    async def ok(code):
        return {"id": 5150, "login": "phone", "avatar_url": "https://avatars.githubusercontent.com/u/5150"}

    monkeypatch.setattr(auth, "github_user", ok)
    with TestClient(app) as c:
        verifier = s_.token_urlsafe(48)
        ch = auth.challenge_of(verifier)
        assert c.get("/auth/github/login?app=short", follow_redirects=False).headers["location"] == "volante://auth?error=bad_request"
        r = c.get(f"/auth/github/login?app={ch}", follow_redirects=False)
        st = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        r = c.get(f"/auth/github/callback?code=x&state={st}", follow_redirects=False)
        loc = urlsplit(r.headers["location"])
        assert (loc.scheme, loc.netloc) == ("volante", "auth") and auth.SESSION_COOKIE not in r.cookies  # no web session
        code = parse_qs(loc.query)["code"][0]
        # a wrong verifier burns the code
        assert c.post("/api/app/session", json={"code": code, "verifier": s_.token_urlsafe(48)}).status_code == 400
        assert c.post("/api/app/session", json={"code": code, "verifier": verifier}).status_code == 400
        # a fresh code + the right verifier -> a session the API accepts as the cookie
        r = c.get(f"/auth/github/login?app={ch}", follow_redirects=False)
        st = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        code = parse_qs(urlsplit(c.get(f"/auth/github/callback?code=x&state={st}", follow_redirects=False).headers["location"]).query)["code"][0]
        r = c.post("/api/app/session", json={"code": code, "verifier": verifier})
        assert r.status_code == 200 and r.json()["user"]["login"] == "phone"
        sid = r.json()["session"]
        assert c.post("/api/app/session", json={"code": code, "verifier": verifier}).status_code == 400  # single use
    with TestClient(app) as phone:  # a fresh client: only the header, like the app's URLSession
        me = phone.get("/api/me", headers={"cookie": f"{auth.SESSION_COOKIE}={sid}"}).json()
        assert me["user"]["login"] == "phone" and me["csrf"]
        # app-flow failures go back to the app, not the website
        r = phone.get(f"/auth/github/login?app={auth.challenge_of('v' * 50)}", follow_redirects=False)
        assert phone.get("/auth/github/callback?error=access_denied", follow_redirects=False).headers["location"] == "volante://auth?error=denied"
