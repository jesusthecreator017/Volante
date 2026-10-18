"""Sessions, CSRF and the GitHub OAuth web flow. Session ids are random 256-bit tokens; the DB stores only their sha256."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from osoarena import config, db
from osoarena.text import safe_login

SESSION_COOKIE = "oso_session"
ANON_COOKIE = "oso_anon"
OAUTH_COOKIE = "oso_oauth"
SESSION_DAYS = 30
PLACEHOLDER_AVATAR = db.DELETED_USER["avatar_url"]


def _secret():
    s = config.get().session_secret
    if len(s) < 32:
        raise RuntimeError("OSO_ARENA_SESSION_SECRET is missing or shorter than 32 characters")
    return s.encode()


def hash_sid(sid):
    return hashlib.sha256(sid.encode()).hexdigest()


def sign(data: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(_secret(), data, hashlib.sha256).digest()).decode().rstrip("=")


def anon_csrf(anon_id):
    return sign(b"anon-csrf:" + anon_id.encode())


def session_user(con, sid):
    if not sid or len(sid) > 100:
        return None, None
    r = con.execute("SELECT s.csrf, s.expires_at, u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=?",
                    (hash_sid(sid),)).fetchone()
    if not r or r["expires_at"] < db.now():
        return None, None
    return r, r["csrf"]


def create_session(con, user_id):
    sid = secrets.token_urlsafe(32)
    t = db.parse_t(db.now())
    con.execute("INSERT INTO sessions (hash, user_id, csrf, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (hash_sid(sid), user_id, secrets.token_urlsafe(32), db.fmt_t(t), db.fmt_t(t + timedelta(days=SESSION_DAYS))))
    return sid


def csrf_ok(expected, got):
    return bool(expected) and bool(got) and hmac.compare_digest(expected.encode(), got.encode())


def safe_next(n):
    """Same-origin paths only: one leading '/', no '//', no backslash, no scheme, no control characters."""
    if not isinstance(n, str) or not n.startswith("/") or n.startswith("//") or "\\" in n or len(n) > 300:
        return None
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in n):
        return None
    p = urlsplit(n)
    if p.scheme or p.netloc:
        return None
    return n


# The iPhone app's sign-in (PKCE-style): the app sends sha256(verifier) as `app`; the callback hands it a one-time code through
# volante://auth?code=...; POST /api/app/session trades code + verifier for a session id. The session never appears in a URL.
APP_SCHEME = "volante"
APP_CODE_TTL_S = 120


def valid_challenge(c):
    return isinstance(c, str) and len(c) == 43 and all(ch.isalnum() or ch in "-_" for ch in c)


def challenge_of(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def oauth_start(next_path, app_challenge=None):
    """-> (authorize URL, cookie value). `app_challenge`: the app flow (see APP_SCHEME)."""
    cfg = config.get()
    state = secrets.token_urlsafe(24)
    d = {"s": state, "n": safe_next(next_path) or "/my-models", "e": int(time.time()) + 600}
    if valid_challenge(app_challenge):
        d["a"] = app_challenge
    payload = json.dumps(d).encode()
    blob = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    cookie = blob + "." + sign(b"oauth:" + blob.encode())
    url = "https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": cfg.github_client_id, "redirect_uri": cfg.base_url + "/auth/github/callback", "scope": "", "state": state,
        "allow_signup": "true"})
    return url, cookie


def oauth_cookie_state(cookie):
    """-> (state, next, app challenge or None) if the cookie is ours and fresh."""
    try:
        blob, sig = cookie.split(".", 1)
        if not hmac.compare_digest(sig, sign(b"oauth:" + blob.encode())):
            return None
        d = json.loads(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)))
        if d["e"] < time.time():
            return None
        return d["s"], safe_next(d["n"]) or "/my-models", d.get("a") if valid_challenge(d.get("a")) else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def valid_avatar(url):
    try:
        p = urlsplit(url)
    except (ValueError, TypeError):
        return PLACEHOLDER_AVATAR
    if p.scheme == "https" and p.hostname == "avatars.githubusercontent.com" and not p.username and not p.password and p.port is None:
        return url[:300]
    return PLACEHOLDER_AVATAR


class SignInError(Exception):
    """Why a GitHub sign-in failed, as a short code that is safe to log and to show (never a token or secret)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


async def github_user(code):
    """Exchange the code; return {id, login, avatar_url} or raise SignInError. The access token is used once and never stored."""
    import httpx
    cfg = config.get()
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.post("https://github.com/login/oauth/access_token", headers={"accept": "application/json"},
                             data={"client_id": cfg.github_client_id, "client_secret": cfg.github_client_secret, "code": code,
                                   "redirect_uri": cfg.base_url + "/auth/github/callback"})
        except httpx.HTTPError as e:
            raise SignInError("github_unreachable") from e
        try:
            body = r.json() if r.status_code == 200 else {}
        except ValueError:
            body = {}
        tok = body.get("access_token")
        if not tok:
            # GitHub answers 200 with {"error": "incorrect_client_credentials" | "bad_verification_code" | "redirect_uri_mismatch" ...}
            err = body.get("error") if isinstance(body.get("error"), str) else f"token_http_{r.status_code}"
            raise SignInError(err[:60])
        try:
            u = await c.get("https://api.github.com/user", headers={"authorization": f"Bearer {tok}",
                                                                     "accept": "application/vnd.github+json"})
        except httpx.HTTPError as e:
            raise SignInError("github_unreachable") from e
        if u.status_code != 200:
            raise SignInError(f"user_http_{u.status_code}")
        d = u.json()
    if type(d.get("id")) is not int or d["id"] <= 0:
        raise SignInError("bad_user")
    return {"id": d["id"], "login": safe_login(d.get("login")), "avatar_url": valid_avatar(d.get("avatar_url"))}
