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


def oauth_start(next_path):
    """-> (authorize URL, cookie value)."""
    cfg = config.get()
    state = secrets.token_urlsafe(24)
    payload = json.dumps({"s": state, "n": safe_next(next_path) or "/my-models", "e": int(time.time()) + 600}).encode()
    blob = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    cookie = blob + "." + sign(b"oauth:" + blob.encode())
    url = "https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": cfg.github_client_id, "redirect_uri": cfg.base_url + "/auth/github/callback", "scope": "", "state": state,
        "allow_signup": "true"})
    return url, cookie


def oauth_cookie_state(cookie):
    """-> (state, next) if the cookie is ours and fresh."""
    try:
        blob, sig = cookie.split(".", 1)
        if not hmac.compare_digest(sig, sign(b"oauth:" + blob.encode())):
            return None
        d = json.loads(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)))
        if d["e"] < time.time():
            return None
        return d["s"], safe_next(d["n"]) or "/my-models"
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


async def github_user(code):
    """Exchange the code; return {id, login, avatar_url} or None. The access token is used once and never stored."""
    import httpx
    cfg = config.get()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post("https://github.com/login/oauth/access_token", headers={"accept": "application/json"},
                         data={"client_id": cfg.github_client_id, "client_secret": cfg.github_client_secret, "code": code,
                               "redirect_uri": cfg.base_url + "/auth/github/callback"})
        tok = r.json().get("access_token") if r.status_code == 200 else None
        if not tok:
            return None
        u = await c.get("https://api.github.com/user", headers={"authorization": f"Bearer {tok}",
                                                                 "accept": "application/vnd.github+json"})
        if u.status_code != 200:
            return None
        d = u.json()
    if type(d.get("id")) is not int or d["id"] <= 0:
        return None
    return {"id": d["id"], "login": safe_login(d.get("login")), "avatar_url": valid_avatar(d.get("avatar_url"))}
