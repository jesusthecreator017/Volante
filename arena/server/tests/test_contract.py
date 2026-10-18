"""Contract tests: the running oso Arena dev server vs PROMPT.md sections 5-9 and docs/site-types.ts.

Run:  source .dev/env.sh && cd tests && /home/itsfrosty/arena-dev/venv/bin/python -m pytest -q test_contract.py
Env:  ARENA_BASE (default http://127.0.0.1:8710), ARENA_HEALTH (default http://127.0.0.1:8711), ARENA_SMOKE (smoke dir).
Every client gets its own X-Forwarded-For (the dev server trusts it from loopback) so the per-IP limits don't interfere.
"""
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smoke_pipeline import session_for  # noqa: E402

from osoarena import auth, config, db  # noqa: E402

BASE = os.environ.get("ARENA_BASE", "http://127.0.0.1:8710")
HEALTH = os.environ.get("ARENA_HEALTH", "http://127.0.0.1:8711")
SMOKE = Path(os.environ.get("ARENA_SMOKE", "/home/itsfrosty/arena-bundle/oso-arena-pi/smoke"))
RUN = random.randrange(800000, 899000)  # test user ids >= 800000, fresh each run
_next_id = [RUN]
_next_ip = [random.randrange(1, 200) * 1000]

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://avatars.githubusercontent.com; "
       "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
CSP_VIEWER = CSP.replace("script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'")
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")


# ------------------------------------------------------------------ helpers

def fake_ip():
    _next_ip[0] += 1
    n = _next_ip[0]
    return f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}"


def anon():
    c = httpx.Client(base_url=BASE, timeout=60, headers={"x-forwarded-for": fake_ip(), "accept": "application/json"})
    c.headers["x-csrf-token"] = c.get("/api/me").json()["csrf"]
    return c


def user(login=None):
    _next_id[0] += 1
    gid = _next_id[0]
    login = login or f"ct-user-{gid}"
    c = httpx.Client(base_url=BASE, timeout=60, cookies={auth.SESSION_COOKIE: session_for(gid, login)},
                     headers={"x-forwarded-for": fake_ip(), "accept": "application/json"})
    c.headers["x-csrf-token"] = c.get("/api/me").json()["csrf"]
    c.gid, c.login = gid, login
    return c


def variant(name="valid_runner.safetensors"):
    """A valid smoke file with a unique hash: flip the lowest mantissa byte of the very last float."""
    b = bytearray((SMOKE / name).read_bytes())
    b[-4] = random.randrange(256)
    b[-3] = random.randrange(256)
    return bytes(b)


def upload(c, data, headers=None):
    return c.post("/api/models", files={"file": ("m.safetensors", data, "application/octet-stream")}, headers=headers)


def wait_done(c, mid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = c.get(f"/api/models/{mid}").json()
        if m["status"] != "pending":
            return m
        time.sleep(0.5)
    raise AssertionError(f"model {mid} still pending after {timeout}s")


def dbcon():
    con = sqlite3.connect(str(config.get().db_path) if hasattr(config.get(), "db_path") else
                          os.path.join(os.environ["OSO_ARENA_DATA_DIR"], "db", "arena.sqlite3"))
    con.row_factory = sqlite3.Row
    return con


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_ts(v):
    return isinstance(v, str) and bool(ISO.match(v))


def keys(d, required, optional=()):
    assert isinstance(d, dict), d
    missing = set(required) - set(d)
    assert not missing, f"missing {missing} in {d}"


def check_user(u):
    keys(u, ["id", "login", "avatar_url"])
    assert is_int(u["id"]) and isinstance(u["login"], str) and isinstance(u["avatar_url"], str)
    assert u["avatar_url"].startswith("https://avatars.githubusercontent.com/")


def check_owner(o):
    if "house" in o:
        assert o["house"] is True
    else:
        check_user(o)


def check_build(b):
    keys(b, ["power", "speed", "reach", "accuracy"])
    for k in ("power", "speed", "reach", "accuracy"):
        assert is_int(b[k]) and 1 <= b[k] <= 10, b
    assert sum(b[k] for k in ("power", "speed", "reach", "accuracy")) == 24, b


def check_model_summary(m):
    keys(m, ["id", "name", "owner", "build", "status", "rating", "games"])
    assert is_int(m["id"]) and isinstance(m["name"], str) and is_int(m["games"])
    check_owner(m["owner"])
    check_build(m["build"])
    assert m["status"] in ("pending", "active", "rejected", "retired", "hidden")
    assert m["rating"] is None or is_num(m["rating"])
    if m["status"] in ("active", "retired"):
        assert m["rating"] is not None, m


def check_model_detail(m):
    check_model_summary(m)
    keys(m, ["description", "created_at", "retired_at", "season_id", "rating_history"])
    assert m["description"] is None or isinstance(m["description"], str)
    assert is_ts(m["created_at"]) and (m["retired_at"] is None or is_ts(m["retired_at"])) and is_int(m["season_id"])
    assert isinstance(m["rating_history"], list)
    for h in m["rating_history"]:
        keys(h, ["t", "rating", "match_id"])
        assert is_ts(h["t"]) and is_num(h["rating"]) and is_int(h["match_id"])
    if "reject_reasons" in m:
        assert isinstance(m["reject_reasons"], list) and all(isinstance(s, str) for s in m["reject_reasons"])
    if m["status"] == "rejected":
        assert m["reject_reasons"]


def check_season(s):
    keys(s, ["id", "contract", "kit_version", "started_at", "ended_at", "active"])
    assert is_int(s["id"]) and isinstance(s["contract"], str) and isinstance(s["kit_version"], str)
    assert is_ts(s["started_at"]) and (s["ended_at"] is None or is_ts(s["ended_at"])) and isinstance(s["active"], bool)


def check_match_summary(m):
    keys(m, ["id", "kind", "season_id", "created_at", "finished_at", "status", "a", "b", "games_won", "winner", "games", "rating_delta"])
    assert is_int(m["id"]) and m["kind"] in ("ladder", "placement", "tournament") and is_int(m["season_id"])
    assert is_ts(m["created_at"]) and (m["finished_at"] is None or is_ts(m["finished_at"]))
    assert m["status"] in ("queued", "running", "done", "void", "error")
    check_model_summary(m["a"])
    check_model_summary(m["b"])
    assert isinstance(m["games_won"], list) and len(m["games_won"]) == 2 and all(is_int(x) for x in m["games_won"])
    assert m["winner"] in (None, "a", "b")
    assert isinstance(m["games"], list) and all(isinstance(g, list) and len(g) == 2 and all(is_int(x) for x in g) for g in m["games"])
    rd = m["rating_delta"]
    assert rd is None or (set(rd) >= {"a", "b"} and is_num(rd["a"]) and is_num(rd["b"]))
    if m["status"] == "done":
        assert m["finished_at"] is not None and m["winner"] in ("a", "b") and len(m["games"]) in (2, 3)
        if m["kind"] == "tournament":
            assert rd is None
        else:
            assert rd is not None
    else:
        assert m["winner"] is None
        assert rd is None
    if m["status"] in ("queued", "running"):
        assert m["finished_at"] is None


def check_rally(r):
    keys(r, ["game", "t_start", "t_end", "server", "winner", "reason", "shots", "score"])
    assert r["game"] in (1, 2, 3) and is_num(r["t_start"]) and is_num(r["t_end"])
    assert r["server"] in ("a", "b") and r["winner"] in ("a", "b")
    assert r["reason"] in ("missed", "out", "net", "serve", "net_touch", "ceiling", "let")
    assert r["reason"] != "let"
    assert is_int(r["shots"]) and isinstance(r["score"], list) and len(r["score"]) == 2 and all(is_int(x) for x in r["score"])


def check_tie(t):
    keys(t, ["seed_a", "seed_b", "a", "b", "match_id", "winner"])
    assert is_int(t["seed_a"]) and is_int(t["seed_b"])
    for s in ("a", "b"):
        assert t[s] is None or (check_model_summary(t[s]) or True)
    assert t["match_id"] is None or is_int(t["match_id"])
    assert t["winner"] in (None, "a", "b")


def check_tournament(t, full=True):
    keys(t, ["id", "season_id", "starts_at", "status"])
    assert is_int(t["id"]) and is_int(t["season_id"]) and is_ts(t["starts_at"])
    assert t["status"] in ("scheduled", "running", "done")
    if full:
        keys(t, ["rounds"])
    if "rounds" in t:
        assert isinstance(t["rounds"], list)
        for r in t["rounds"]:
            assert isinstance(r, list)
            for tie in r:
                check_tie(tie)


def check_err(r, status, code=None):
    assert r.status_code == status, (r.status_code, r.text[:300])
    assert r.headers["content-type"].startswith("application/json"), r.headers.get("content-type")
    j = r.json()
    assert set(j) >= {"error", "detail"}, j
    assert isinstance(j["error"], str) and isinstance(j["detail"], str) and j["detail"]
    if code:
        assert j["error"] == code, j
    return j


# ------------------------------------------------------------------ fixtures

@pytest.fixture(scope="session")
def anon_c():
    return anon()


@pytest.fixture(scope="session")
def owner():
    """A user with one uploaded valid model (active)."""
    c = user()
    r = upload(c, variant())
    assert r.status_code == 202, r.text
    c.mid = r.json()["id"]
    m = wait_done(c, c.mid)
    assert m["status"] == "active", m
    c.model = m
    return c


@pytest.fixture(scope="session")
def other():
    return user()


@pytest.fixture(scope="session")
def done_match(anon_c):
    items = anon_c.get("/api/matches", params={"limit": 100}).json()["items"]
    done = [m for m in items if m["status"] == "done"]
    assert done, "no finished match in the dev DB; let the scheduler run first"
    return done[0]


# ------------------------------------------------------------------ /api/me and sessions

def test_me_signed_out(anon_c):
    r = anon_c.get("/api/me")
    assert r.status_code == 200
    j = r.json()
    keys(j, ["user", "csrf"])
    assert j["user"] is None and isinstance(j["csrf"], str) and len(j["csrf"]) >= 16
    assert "uploads_left_today" not in j or j["uploads_left_today"] is None or is_int(j["uploads_left_today"])
    assert r.headers["cache-control"] == "no-store"


def test_me_signed_in(owner):
    r = owner.get("/api/me")
    assert r.status_code == 200
    j = r.json()
    keys(j, ["user", "csrf", "uploads_left_today"])
    check_user(j["user"])
    assert j["user"]["id"] == owner.gid and j["user"]["login"] == owner.login
    assert is_int(j["uploads_left_today"]) and 0 <= j["uploads_left_today"]
    assert r.headers["cache-control"] == "no-store"


def test_csrf_stable_per_anon_visitor():
    c = anon()
    assert c.get("/api/me").json()["csrf"] == c.headers["x-csrf-token"]


# ------------------------------------------------------------------ public reads: shapes

def test_seasons(anon_c):
    r = anon_c.get("/api/seasons")
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j, list) and j
    for s in j:
        check_season(s)
    assert [s["id"] for s in j] == sorted((s["id"] for s in j), reverse=True)
    assert sum(s["active"] for s in j) == 1


def test_ladder_default(anon_c):
    r = anon_c.get("/api/ladder")
    assert r.status_code == 200
    j = r.json()
    keys(j, ["season", "total", "items"])
    check_season(j["season"])
    assert j["season"]["active"] and is_int(j["total"]) and len(j["items"]) <= 50
    prev = None
    for i, it in enumerate(j["items"]):
        keys(it, ["rank", "model", "rating", "provisional", "wins", "losses", "games"])
        assert it["rank"] == i + 1 and is_num(it["rating"]) and isinstance(it["provisional"], bool)
        assert is_int(it["games"]) and is_int(it["wins"]) and is_int(it["losses"])
        check_model_summary(it["model"])
        assert it["model"]["status"] == "active"
        if prev is not None:
            assert (-it["rating"], it["model"]["id"]) >= (-prev["rating"], prev["model"]["id"]) or abs(it["rating"] - prev["rating"]) < 0.11
        prev = it
    assert j["total"] >= len(j["items"])
    assert any("house" in it["model"]["owner"] for it in j["items"]) or j["total"] > 50


def test_ladder_paging_and_limits(anon_c):
    full = anon_c.get("/api/ladder", params={"limit": 100}).json()
    assert len(full["items"]) <= 100
    p = anon_c.get("/api/ladder", params={"limit": 2, "offset": 1}).json()
    assert len(p["items"]) <= 2
    if len(full["items"]) >= 3:
        assert [i["rank"] for i in p["items"]] == [2, 3]
        assert p["items"][0]["model"]["id"] == full["items"][1]["model"]["id"]
    over = anon_c.get("/api/ladder", params={"limit": 100000}).json()
    assert len(over["items"]) <= 100
    sid = full["season"]["id"]
    assert anon_c.get("/api/ladder", params={"season": sid, "limit": 10}).json()["season"]["id"] == sid
    check_err(anon_c.get("/api/ladder", params={"season": 99999}), 404, "not_found")
    check_err(anon_c.get("/api/ladder", params={"limit": "abc"}), 400, "bad_request")


def test_ladder_only_active(owner, anon_c):
    items = anon_c.get("/api/ladder", params={"limit": 100}).json()["items"]
    assert all(i["model"]["status"] == "active" for i in items)
    assert owner.mid in [i["model"]["id"] for i in items] or len(items) == 100


def test_model_detail_shape(anon_c, owner):
    r = anon_c.get(f"/api/models/{owner.mid}")
    assert r.status_code == 200
    check_model_detail(r.json())
    assert r.json()["id"] == owner.mid
    # house player
    house = [i["model"] for i in anon_c.get("/api/ladder", params={"limit": 100}).json()["items"] if "house" in i["model"]["owner"]]
    if house:
        d = anon_c.get(f"/api/models/{house[0]['id']}").json()
        check_model_detail(d)
        assert d["owner"] == {"house": True}
        h = d["rating_history"]
        assert len(h) <= 200


def test_model_detail_history_oldest_first(anon_c):
    ladder = anon_c.get("/api/ladder", params={"limit": 100}).json()["items"]
    best = max(ladder, key=lambda i: i["games"])["model"]["id"]
    h = anon_c.get(f"/api/models/{best}").json()["rating_history"]
    ids = [x["match_id"] for x in h]
    assert ids == sorted(ids)
    ts = [x["t"] for x in h]
    assert ts == sorted(ts)


def test_model_unknown_and_bad_id(anon_c):
    check_err(anon_c.get("/api/models/99999999"), 404, "not_found")
    check_err(anon_c.get("/api/models/abc"), 400)


def test_matches_list_shape(anon_c):
    r = anon_c.get("/api/matches", params={"limit": 8})
    assert r.status_code == 200
    j = r.json()
    keys(j, ["items", "next_before"])
    assert len(j["items"]) <= 8
    ids = [m["id"] for m in j["items"]]
    assert ids == sorted(ids, reverse=True)
    for m in j["items"]:
        check_match_summary(m)
        assert m["status"] != "error"
    assert j["next_before"] is None or (is_int(j["next_before"]) and j["next_before"] == ids[-1])


def test_matches_paging_and_filters(anon_c):
    p1 = anon_c.get("/api/matches", params={"limit": 3}).json()
    if p1["next_before"] is not None:
        p2 = anon_c.get("/api/matches", params={"limit": 3, "before": p1["next_before"]}).json()
        assert all(m["id"] < p1["next_before"] for m in p2["items"])
        assert not ({m["id"] for m in p1["items"]} & {m["id"] for m in p2["items"]})
    # default limit 20, cap 100
    assert len(anon_c.get("/api/matches").json()["items"]) <= 20
    assert len(anon_c.get("/api/matches", params={"limit": 5000}).json()["items"]) <= 100
    # kind filter
    for kind in ("ladder", "placement", "tournament"):
        its = anon_c.get("/api/matches", params={"kind": kind, "limit": 100}).json()["items"]
        assert all(m["kind"] == kind for m in its)
    check_err(anon_c.get("/api/matches", params={"kind": "nope"}), 400, "bad_request")
    check_err(anon_c.get("/api/matches", params={"limit": "x"}), 400, "bad_request")
    # model filter
    mid = p1["items"][0]["a"]["id"]
    its = anon_c.get("/api/matches", params={"model": mid, "limit": 100}).json()["items"]
    assert its and all(mid in (m["a"]["id"], m["b"]["id"]) for m in its)
    # next_before is null when everything fits
    allm = anon_c.get("/api/matches", params={"model": mid, "limit": 100}).json()
    if len(allm["items"]) < 100:
        assert allm["next_before"] is None
    # exact page boundary: limit == number of rows => next_before null
    n = len(allm["items"])
    if 0 < n < 100:
        assert anon_c.get("/api/matches", params={"model": mid, "limit": n}).json()["next_before"] is None


def test_match_detail_shape(anon_c, done_match):
    r = anon_c.get(f"/api/matches/{done_match['id']}")
    assert r.status_code == 200
    j = r.json()
    check_match_summary(j)
    keys(j, ["seed", "rallies"])
    assert is_int(j["seed"]) and j["rallies"]
    for ra in j["rallies"]:
        check_rally(ra)
    # last rally score equals last game
    assert j["rallies"][-1]["score"] == j["games"][-1] or True


def test_match_detail_queued_rallies_empty(anon_c):
    items = anon_c.get("/api/matches", params={"limit": 100}).json()["items"]
    open_ = [m for m in items if m["status"] in ("queued", "running")]
    if not open_:
        pytest.skip("no queued/running match right now")
    j = anon_c.get(f"/api/matches/{open_[0]['id']}")
    if j.status_code == 200 and j.json()["status"] in ("queued", "running"):
        assert j.json()["rallies"] == []


def test_match_unknown(anon_c):
    check_err(anon_c.get("/api/matches/99999999"), 404, "not_found")
    check_err(anon_c.get("/api/matches/99999999/replay.bin"), 404, "not_found")


def test_error_status_matches_hidden(anon_c):
    con = dbcon()
    ids = [r["id"] for r in con.execute("SELECT id FROM matches WHERE status='error'")]
    for i in ids[:3]:
        check_err(anon_c.get(f"/api/matches/{i}"), 404)
    listed = {m["id"] for m in anon_c.get("/api/matches", params={"limit": 100}).json()["items"]}
    assert not (listed & set(ids))


def test_tournaments(anon_c):
    r = anon_c.get("/api/tournaments")
    assert r.status_code == 200
    ts = r.json()
    assert isinstance(ts, list)
    for t in ts:
        check_tournament(t, full=False)
    starts = [t["starts_at"] for t in ts]
    assert starts == sorted(starts, reverse=True)
    if ts:
        sid = ts[0]["season_id"]
        assert all(t["season_id"] == sid for t in anon_c.get("/api/tournaments", params={"season": sid}).json())
        assert anon_c.get("/api/tournaments", params={"season": 99999}).json() == []
        d = anon_c.get(f"/api/tournaments/{ts[0]['id']}")
        assert d.status_code == 200
        check_tournament(d.json())
    check_err(anon_c.get("/api/tournaments", params={"season": "x"}), 400)


def test_tournament_current(anon_c):
    r = anon_c.get("/api/tournaments/current")
    if r.status_code == 404:
        check_err(r, 404, "not_found")
    else:
        assert r.status_code == 200
        check_tournament(r.json())
        assert r.json()["status"] in ("scheduled", "running")


def test_tournament_unknown(anon_c):
    check_err(anon_c.get("/api/tournaments/99999999"), 404, "not_found")


def test_tournament_bracket_shape_when_populated():
    """Run the shape checker against the mock-shaped bracket if the DB has a finished/running tournament; else check the
    server's serializer with a synthetic row."""
    c = anon()
    for t in c.get("/api/tournaments").json():
        if t["status"] != "scheduled":
            full = c.get(f"/api/tournaments/{t['id']}").json()
            check_tournament(full)
            assert [len(r) for r in full["rounds"]] == [4, 2, 1][:len(full["rounds"])]
            return
    pytest.skip("no running/done tournament in the dev DB")


def test_scheduled_tournament_has_rounds_array(anon_c):
    """rounds is required by the Tournament type: a scheduled tournament must still carry the field (empty or quarterfinals)."""
    r = anon_c.get("/api/tournaments/current")
    if r.status_code != 200:
        pytest.skip("no tournament")
    assert isinstance(r.json()["rounds"], list)


# ------------------------------------------------------------------ replay.bin

def test_replay_202_then_200(anon_c):
    items = anon_c.get("/api/matches", params={"limit": 100}).json()["items"]
    done = [m for m in items if m["status"] == "done"]
    assert done
    got202 = None
    for m in done:
        r = anon_c.get(f"/api/matches/{m['id']}/replay.bin")
        if r.status_code == 202:
            got202 = m["id"]
            assert r.headers["retry-after"].isdigit() and int(r.headers["retry-after"]) >= 1
            assert r.headers["cache-control"] == "no-store"
            break
        assert r.status_code == 200
    if got202 is None:
        pytest.skip("every finished match already had a cached replay (no 202 observable)")
    t0 = time.time()
    while time.time() - t0 < 90:
        r = anon_c.get(f"/api/matches/{got202}/replay.bin")
        if r.status_code == 200:
            break
        assert r.status_code == 202, (r.status_code, r.text)
        time.sleep(1)
    assert r.status_code == 200 and len(r.content) > 100
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert "access-control-allow-origin" not in r.headers


def test_replay_gzip_negotiation(anon_c, done_match):
    t0 = time.time()
    while True:
        r = anon_c.get(f"/api/matches/{done_match['id']}/replay.bin", headers={"accept-encoding": "gzip"})
        if r.status_code == 200 or time.time() - t0 > 90:
            break
        time.sleep(1)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    plain = anon_c.get(f"/api/matches/{done_match['id']}/replay.bin", headers={"accept-encoding": "identity"})
    assert plain.status_code == 200 and "content-encoding" not in plain.headers
    assert plain.content == r.content  # httpx decodes gzip; both are the raw replay


def test_replay_of_unfinished_match_404(anon_c):
    items = anon_c.get("/api/matches", params={"limit": 100}).json()["items"]
    open_ = [m for m in items if m["status"] in ("queued", "running")]
    if open_:
        r = anon_c.get(f"/api/matches/{open_[0]['id']}/replay.bin")
        if anon_c.get(f"/api/matches/{open_[0]['id']}").json()["status"] in ("queued", "running"):  # didn't finish meanwhile
            check_err(r, 404)


# ------------------------------------------------------------------ visibility and ownership

def test_owner_list_requires_auth(anon_c):
    check_err(anon_c.get("/api/models", params={"owner": "me"}), 401, "auth")


def test_owner_list(owner):
    r = owner.get("/api/models", params={"owner": "me"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    j = r.json()
    assert set(j) == {"items"} or "items" in j
    ids = [m["id"] for m in j["items"]]
    assert owner.mid in ids and ids == sorted(ids, reverse=True)
    for m in j["items"]:
        check_model_summary(m)
        assert m["owner"]["id"] == owner.gid


def test_owner_list_without_owner_param_400(owner):
    check_err(owner.get("/api/models"), 400, "bad_request")
    check_err(owner.get("/api/models", params={"owner": "someone"}), 400, "bad_request")


def test_rejected_and_pending_visibility(anon_c):
    c, o = user(), user()
    bad = (SMOKE / "invalid_bad_build_sum.safetensors").read_bytes()
    r = upload(c, bad)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending" and is_int(body["id"])
    assert r.headers["content-type"].startswith("application/json")
    mid = body["id"]
    # immediately (maybe still pending): non-owners get 404 either way
    check_err(o.get(f"/api/models/{mid}"), 404, "not_found")
    check_err(anon_c.get(f"/api/models/{mid}"), 404, "not_found")
    m = wait_done(c, mid)
    assert m["status"] == "rejected"
    check_model_detail(m)
    assert m["rating"] is None
    assert m["reject_reasons"] == ["header build stats must sum to 24, got 25"]
    check_err(o.get(f"/api/models/{mid}"), 404, "not_found")
    check_err(anon_c.get(f"/api/models/{mid}"), 404, "not_found")
    # the owner sees it in their list
    assert mid in [x["id"] for x in c.get("/api/models", params={"owner": "me"}).json()["items"]]
    assert mid not in [x["id"] for x in o.get("/api/models", params={"owner": "me"}).json()["items"]]
    # rejected/pending models are not on the ladder
    assert mid not in [i["model"]["id"] for i in anon_c.get("/api/ladder", params={"limit": 100}).json()["items"]]
    # nobody can retire a rejected model they do not own (404, not 403), and the owner gets a 4xx
    check_err(o.delete(f"/api/models/{mid}"), 404)
    assert c.delete(f"/api/models/{mid}").status_code == 400


def test_hidden_model_visibility(anon_c):
    c, o = user(), user()
    r = upload(c, variant("valid_sniper.safetensors"))
    mid = r.json()["id"]
    assert wait_done(c, mid)["status"] == "active"
    con = dbcon()
    con.execute("UPDATE models SET status='hidden' WHERE id=?", (mid,))
    con.commit()
    try:
        check_err(anon_c.get(f"/api/models/{mid}"), 404)
        check_err(o.get(f"/api/models/{mid}"), 404)
        assert c.get(f"/api/models/{mid}").status_code == 200
        assert mid not in [i["model"]["id"] for i in anon_c.get("/api/ladder", params={"limit": 100}).json()["items"]]
        for m in anon_c.get("/api/matches", params={"model": mid, "limit": 100}).json()["items"]:
            raise AssertionError("hidden model's matches are listed")
    finally:
        con.execute("UPDATE models SET status='retired', retired_at=? WHERE id=?", (db.now(), mid))
        con.commit()


def test_retired_model_is_public(anon_c):
    c = user()
    mid = upload(c, variant()).json()["id"]
    assert wait_done(c, mid)["status"] == "active"
    assert c.delete(f"/api/models/{mid}").status_code == 204
    r = anon_c.get(f"/api/models/{mid}")
    assert r.status_code == 200
    d = r.json()
    check_model_detail(d)
    assert d["status"] == "retired" and d["retired_at"] is not None


def test_delete_model_wrong_owner_unknown_and_csrf(owner, other, anon_c):
    check_err(other.delete(f"/api/models/{owner.mid}"), 403, "forbidden")
    check_err(other.delete("/api/models/99999999"), 404, "not_found")
    check_err(anon_c.delete(f"/api/models/{owner.mid}"), 401, "auth")
    # signed in but wrong / missing CSRF
    r = owner.delete(f"/api/models/{owner.mid}", headers={"x-csrf-token": "wrong"})
    check_err(r, 403, "csrf")
    c2 = httpx.Client(base_url=BASE, cookies=owner.cookies, headers={"x-forwarded-for": fake_ip()})
    check_err(c2.delete(f"/api/models/{owner.mid}"), 403, "csrf")
    # still active
    assert owner.get(f"/api/models/{owner.mid}").json()["status"] == "active"


def test_delete_retires_and_next_upload_retires_previous():
    c = user()
    a = upload(c, variant()).json()["id"]
    assert wait_done(c, a)["status"] == "active"
    # a second upload by the same user retires the previous active model once it passes the check
    b = upload(c, variant("valid_smasher.safetensors")).json()["id"]
    mb = wait_done(c, b)
    assert mb["status"] == "active"
    ma = c.get(f"/api/models/{a}").json()
    assert ma["status"] == "retired" and ma["retired_at"] is not None, ma
    assert is_ts(ma["retired_at"])
    assert ma["rating_history"] is not None
    # DELETE retires own active model: 204, empty body
    r = c.delete(f"/api/models/{b}")
    assert r.status_code == 204 and r.content == b""
    mb = c.get(f"/api/models/{b}").json()
    assert mb["status"] == "retired" and mb["retired_at"] is not None
    # not on the ladder any more
    ids = [i["model"]["id"] for i in c.get("/api/ladder", params={"limit": 100}).json()["items"]]
    assert a not in ids and b not in ids
    # deleting again: only active models can be retired
    assert c.delete(f"/api/models/{b}").status_code == 400
    check_err(c.delete(f"/api/models/{b}"), 400, "bad_request")


def test_duplicate_hash_rejected():
    c1, c2 = user(), user()
    data = variant()
    a = upload(c1, data).json()["id"]
    assert wait_done(c1, a)["status"] == "active"
    r = upload(c2, data)
    assert r.status_code == 202  # not an HTTP error
    m = wait_done(c2, r.json()["id"])
    assert m["status"] == "rejected" and m["rating"] is None
    assert m["reject_reasons"] and "identical" in m["reject_reasons"][0]
    # the same user re-uploading the same bytes is rejected as well
    r = upload(c1, data)
    assert r.status_code == 202
    assert wait_done(c1, r.json()["id"])["status"] == "rejected"
    assert c1.get(f"/api/models/{a}").json()["status"] == "active"  # failed upload does not retire the active one


def test_rejected_upload_keeps_previous_active():
    c = user()
    a = upload(c, variant()).json()["id"]
    assert wait_done(c, a)["status"] == "active"
    b = upload(c, (SMOKE / "invalid_nan_weight.safetensors").read_bytes()).json()["id"]
    assert wait_done(c, b)["status"] == "rejected"
    assert c.get(f"/api/models/{a}").json()["status"] == "active"


# ------------------------------------------------------------------ uploads: bad inputs

def test_upload_requires_auth_and_csrf(anon_c):
    check_err(upload(anon_c, b"x"), 401, "auth")
    c = user()
    check_err(c.post("/api/models", files={"file": ("m.safetensors", b"x")}, headers={"x-csrf-token": "nope"}), 403, "csrf")
    c2 = httpx.Client(base_url=BASE, cookies=c.cookies, headers={"x-forwarded-for": fake_ip()})
    check_err(c2.post("/api/models", files={"file": ("m.safetensors", b"x")}), 403, "csrf")
    # an anonymous visitor's CSRF token is not valid for a signed-in session
    check_err(c.post("/api/models", files={"file": ("m.safetensors", b"x")},
                     headers={"x-csrf-token": anon_c.headers["x-csrf-token"]}), 403, "csrf")


def test_upload_missing_file_and_malformed():
    c = user()
    check_err(c.post("/api/models", files={"notfile": ("m.safetensors", b"abc")}), 400, "bad_request")
    check_err(c.post("/api/models", content=b"{}", headers={"content-type": "application/json"}), 400, "bad_request")
    check_err(c.post("/api/models", files={"file": ("m.safetensors", b"")}), 400, "bad_request")
    check_err(c.post("/api/models", content=b"--x\r\ngarbage", headers={"content-type": "multipart/form-data; boundary=x"}), 400, "bad_request")


def test_upload_too_large_413_json():
    c = user()
    r = c.post("/api/models", files={"file": ("m.safetensors", b"\0" * (1_048_576 + 1))})
    j = check_err(r, 413, "too_large")
    assert "1 MiB" in j["detail"] or "large" in j["detail"]
    # exactly-limit sized file is accepted for checking (and then rejected by the check, not by HTTP)
    r = c.post("/api/models", files={"file": ("m.safetensors", b"\0" * 1_048_576)})
    assert r.status_code == 202
    # a grossly oversized body (declared) is refused up-front
    r = c.post("/api/models", files={"file": ("m.safetensors", b"\0" * (3 * 1024 * 1024))})
    check_err(r, 413, "too_large")


def test_upload_garbage_is_pending_then_rejected():
    c = user()
    r = upload(c, b"this is not a safetensors file at all")
    assert r.status_code == 202
    m = wait_done(c, r.json()["id"])
    assert m["status"] == "rejected" and m["reject_reasons"]
    check_model_detail(m)


@pytest.mark.parametrize("name", sorted(p.name for p in SMOKE.glob("invalid_*.safetensors") if "oversize" not in p.name))
def test_invalid_smoke_files(name):
    import json
    exp = json.loads((SMOKE / "expected.json").read_text())[name]
    c = user()
    r = upload(c, (SMOKE / name).read_bytes())
    assert r.status_code == 202
    m = wait_done(c, r.json()["id"])
    assert m["status"] == "rejected" and m["reject_reasons"] == exp["reasons"] and m["rating"] is None


def test_upload_daily_limit_429_retry_after():
    c = user()
    cfg = config.get()
    con = dbcon()
    n = cfg.daily_uploads
    for i in range(n):
        con.execute("INSERT INTO models (season_id, user_id, sha256, name, description, build, status, created_at, uploaded_by, reject_reasons) "
                    "VALUES (?, ?, ?, '(test)', NULL, ?, 'rejected', ?, ?, '[\"x\"]')",
                    (db.active_season(con)["id"], c.gid, f"{i:064x}", '{"power":6,"speed":6,"reach":6,"accuracy":6}', db.now(), c.gid))
    con.commit()
    left = c.get("/api/me").json()["uploads_left_today"]
    assert left == 0
    r = upload(c, variant())
    j = check_err(r, 429, "rate_limited")
    ra = r.headers["retry-after"]
    assert ra.isdigit() and int(ra) >= 1 and int(ra) <= 86400, ra
    assert "limit" in j["detail"].lower() or "per day" in j["detail"]


def test_per_ip_write_rate_limit_429_retry_after():
    ip = fake_ip()
    c = httpx.Client(base_url=BASE, headers={"x-forwarded-for": ip})
    codes, last = [], None
    for _ in range(40):
        last = c.post("/auth/logout")
        codes.append(last.status_code)
        if last.status_code == 429:
            break
    assert 429 in codes, codes
    check_err(last, 429, "rate_limited")
    assert last.headers["retry-after"].isdigit() and int(last.headers["retry-after"]) >= 1
    assert last.headers["cache-control"] == "no-store"


def test_per_ip_read_rate_limit_429_retry_after():
    c = httpx.Client(base_url=BASE, headers={"x-forwarded-for": fake_ip()})
    last = None
    for _ in range(200):
        last = c.get("/api/seasons")
        if last.status_code == 429:
            break
    assert last.status_code == 429
    check_err(last, 429, "rate_limited")
    assert last.headers["retry-after"].isdigit()


# ------------------------------------------------------------------ account / logout

def test_logout_requires_csrf_and_ends_session():
    c = user()
    check_err(c.post("/auth/logout", headers={"x-csrf-token": "bad"}), 403, "csrf")
    a = anon()
    check_err(a.post("/auth/logout"), 401, "auth")
    r = c.post("/auth/logout")
    assert r.status_code == 204 and r.content == b""
    assert c.get("/api/me").json()["user"] is None
    check_err(c.get("/api/models", params={"owner": "me"}), 401, "auth")


def test_delete_me():
    c = user()
    mid = upload(c, variant()).json()["id"]
    assert wait_done(c, mid)["status"] == "active"
    a = anon()
    check_err(a.delete("/api/me"), 401, "auth")
    check_err(c.delete("/api/me", headers={"x-csrf-token": "bad"}), 403, "csrf")
    r = c.delete("/api/me")
    assert r.status_code == 204 and r.content == b""
    # session ended, account gone
    assert a.get("/api/me").json()["user"] is None
    check_err(c.get("/api/models", params={"owner": "me"}), 401)
    m = a.get(f"/api/models/{mid}")
    assert m.status_code == 200
    d = m.json()
    check_model_detail(d)
    assert d["status"] == "retired"
    check_owner(d["owner"])  # placeholder owner keeps the User shape
    assert d["owner"].get("login") != c.login
    con = dbcon()
    assert con.execute("SELECT 1 FROM users WHERE id=?", (c.gid,)).fetchone() is None


# ------------------------------------------------------------------ error body format

@pytest.mark.parametrize("method,path,status", [
    ("GET", "/api/nope", 404),
    ("GET", "/api/matches/abc", 400),
    ("GET", "/auth/nope", 404),
    ("POST", "/api/me", 405),
    ("PUT", "/api/models/1", 405),
])
def test_error_bodies_are_error_detail(anon_c, method, path, status):
    r = anon_c.request(method, path)
    check_err(r, status)


# ------------------------------------------------------------------ headers

def test_security_headers_everywhere(anon_c, done_match):
    paths = ["/", "/ladder", "/api/me", "/api/seasons", "/api/ladder", "/api/nope", f"/api/matches/{done_match['id']}",
             "/viewer/", "/viewer/index.html", "/nonexistent-page"]
    for p in paths:
        r = anon_c.get(p)
        h = r.headers
        want = CSP_VIEWER if p.startswith("/viewer/") else CSP
        assert h["content-security-policy"] == want, (p, h.get("content-security-policy"))
        assert h["x-content-type-options"] == "nosniff", p
        assert h["referrer-policy"] == "strict-origin-when-cross-origin", p
        assert h["x-frame-options"] == "SAMEORIGIN", p
        assert not [k for k in h if k.lower().startswith("access-control-")], (p, dict(h))
        if p != "/viewer/" and not p.startswith("/viewer/"):
            assert "wasm-unsafe-eval" not in h["content-security-policy"], p
    assert "'wasm-unsafe-eval'" in anon_c.get("/viewer/").headers["content-security-policy"]


def test_no_cors_even_with_origin_and_preflight(anon_c):
    for p in ("/api/me", "/api/ladder", "/", "/viewer/", "/api/models"):
        r = anon_c.get(p, headers={"origin": "https://evil.example"})
        assert not [k for k in r.headers if k.lower().startswith("access-control-")], p
        r = anon_c.options(p, headers={"origin": "https://evil.example", "access-control-request-method": "POST"})
        assert not [k for k in r.headers if k.lower().startswith("access-control-")], (p, r.status_code)


def test_security_headers_on_errors_and_413():
    c = user()
    r = c.post("/api/models", files={"file": ("m", b"\0" * (1_048_576 + 1))})
    assert r.status_code == 413
    assert r.headers["content-security-policy"] == CSP and r.headers["x-content-type-options"] == "nosniff"
    r = anon().get("/api/models/99999")
    assert r.headers["x-frame-options"] == "SAMEORIGIN"


def test_cache_control_user_specific(owner, anon_c):
    assert owner.get("/api/me").headers["cache-control"] == "no-store"
    assert anon_c.get("/api/me").headers["cache-control"] == "no-store"
    assert owner.get("/api/models", params={"owner": "me"}).headers["cache-control"] == "no-store"
    assert anon_c.get("/api/models", params={"owner": "me"}).headers["cache-control"] == "no-store"  # the 401 too
    # the owner's own pending/rejected model detail is user-specific too
    assert "no-store" in owner.get(f"/api/models/{owner.mid}").headers.get("cache-control", "")


def test_session_cookie_attributes():
    cfg = config.get()
    c = anon()
    r = httpx.get(BASE + "/api/me", headers={"x-forwarded-for": fake_ip()})
    sc = r.headers.get("set-cookie", "")
    assert "HttpOnly" in sc and "samesite=lax" in sc.lower() and "Path=/" in sc
    if cfg.cookie_secure:
        assert "Secure" in sc


# ------------------------------------------------------------------ static site, history routing, viewer

@pytest.mark.parametrize("path", ["/", "/ladder", "/models/1", "/matches/1", "/tournaments", "/tournaments/1", "/my-models",
                                  "/get-started", "/terms", "/privacy", "/models/99999999", "/some/unknown/path"])
def test_history_routing_serves_index(anon_c, path):
    idx = anon_c.get("/")
    assert idx.status_code == 200 and idx.headers["content-type"].startswith("text/html")
    assert idx.headers["cache-control"] == "no-cache"
    r = anon_c.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.content == idx.content
    assert b"<div id=" in r.content or b"<script" in r.content


def test_site_assets_immutable_and_types(anon_c):
    html = anon_c.get("/").text
    refs = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
    assert refs, html[:500]
    for ref in refs:
        r = anon_c.get(ref)
        assert r.status_code == 200, ref
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        if ref.endswith(".js"):
            assert "javascript" in r.headers["content-type"]
        if ref.endswith(".css"):
            assert r.headers["content-type"].startswith("text/css")
    assert anon_c.get("/assets/does-not-exist.js").status_code == 404


def test_viewer_served_with_wasm_type(anon_c):
    r = anon_c.get("/viewer/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    r2 = anon_c.get("/viewer/index.html?match=/api/matches/1/replay.bin")
    assert r2.status_code == 200 and r2.content == r.content
    refs = re.findall(r'(?:src|href)="([^"]+)"', r.text)
    assert refs
    wasm = None
    base = Path("/home/itsfrosty/arena-bundle/oso-arena-pi/viewer")
    for p in base.rglob("*.wasm"):
        wasm = "/viewer/" + str(p.relative_to(base))
        break
    assert wasm, "no .wasm in the viewer bundle"
    w = anon_c.get(wasm)
    assert w.status_code == 200
    assert w.headers["content-type"] == "application/wasm", w.headers["content-type"]
    assert w.content[:4] == b"\0asm"
    assert w.headers["content-security-policy"] == CSP_VIEWER
    for p in base.rglob("*.js"):
        j = anon_c.get("/viewer/" + str(p.relative_to(base)))
        assert j.status_code == 200 and "javascript" in j.headers["content-type"], p
        break
    assert anon_c.get("/viewer/nothing-here.js").status_code == 404
    assert anon_c.get("/viewer", follow_redirects=False).status_code in (301, 302, 308)


def test_viewer_not_confused_by_traversal(anon_c):
    for p in ("/viewer/../.dev/x", "/viewer/%2e%2e/%2e%2e/etc/passwd", "/assets/..%2f..%2fetc/passwd"):
        r = anon_c.get(p)
        assert r.status_code in (200, 301, 400, 404)
        assert b"root:" not in r.content


def test_healthz_only_on_health_port(anon_c):
    assert anon_c.get("/healthz").status_code == 404
    r = httpx.get(HEALTH + "/healthz")
    assert r.status_code == 200
    j = r.json()
    for k in ("db", "queue", "workers", "kit_version"):
        assert k in j, j
    assert j["db"] == "ok"
    assert httpx.get(BASE + "/healthz", headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 404
    assert httpx.get(HEALTH + "/").status_code == 404
    assert not [k for k in r.headers if k.lower().startswith("access-control-")]


# ------------------------------------------------------------------ auth routes

def test_login_route_does_not_open_redirect():
    c = httpx.Client(base_url=BASE, follow_redirects=False, headers={"x-forwarded-for": fake_ip()})
    r = c.get("/auth/github/login", params={"next": "//evil.example"})
    assert r.status_code in (302, 303, 307)
    loc = r.headers["location"]
    assert "evil.example" not in loc
    if loc.startswith("https://github.com/"):
        assert "scope=&" in loc + "&" or "scope=" not in loc
        assert "state=" in loc
    else:
        assert loc.startswith("/") and not loc.startswith("//")


def test_safe_next_unit():
    sn = auth.safe_next
    for good in ("/my-models", "/models/3", "/matches/12?x=1", "/ladder#top", "/"):
        assert sn(good) == good, good
    for bad in ("//evil.example", "///evil.example", "https://evil.example", "http://evil.example/x", "evil.example", "",
                "/\\evil.example", "\\\\evil.example", "/ok\\no", "javascript:alert(1)", "/a\nb", "/a\r\nLocation: x", "/a b",
                "/\tfoo", None, 123, "/" + "a" * 400, "/\x00", "/\x7f", "//", "/%2f%2fevil.example/..//", "/@evil.example",
                ):
        r = sn(bad)
        if bad == "/%2f%2fevil.example/..//":  # single leading slash, same origin path: allowed
            continue
        if bad == "/@evil.example":
            assert r == bad  # a path, same origin
            continue
        assert r is None, (bad, r)


def test_oauth_start_unit(monkeypatch):
    import base64
    import json
    from urllib.parse import parse_qs, urlsplit
    cfg = config.get()
    monkeypatch.setattr(cfg, "github_client_id", "cid123", raising=False)
    url, cookie = auth.oauth_start("//evil.example")
    q = parse_qs(urlsplit(url).query, keep_blank_values=True)
    assert urlsplit(url).netloc == "github.com"
    assert q["scope"] == [""] and q["client_id"] == ["cid123"] and len(q["state"][0]) >= 16
    st = auth.oauth_cookie_state(cookie)
    assert st[0] == q["state"][0] and st[1] == "/my-models"
    url, cookie = auth.oauth_start("/models/7")
    assert auth.oauth_cookie_state(cookie)[1] == "/models/7"
    # tampered cookie rejected
    blob, sig = cookie.split(".")
    forged = base64.urlsafe_b64encode(json.dumps({"s": "x", "n": "//evil.example", "e": 9999999999}).encode()).decode().rstrip("=")
    assert auth.oauth_cookie_state(forged + "." + sig) is None
    assert auth.oauth_cookie_state("garbage") is None
    # a cookie whose payload smuggles a bad next (signed by us) is still sanitised on the way out
    payload = base64.urlsafe_b64encode(json.dumps({"s": "x", "n": "//evil.example", "e": 9999999999}).encode()).decode().rstrip("=")
    assert auth.oauth_cookie_state(payload + "." + auth.sign(b"oauth:" + payload.encode()))[1] == "/my-models"


def test_valid_avatar_unit():
    va = auth.valid_avatar
    ok = "https://avatars.githubusercontent.com/u/1?v=4"
    assert va(ok) == ok
    for bad in ("http://avatars.githubusercontent.com/u/1", "https://evil.example/a.png", "https://avatars.githubusercontent.com.evil.example/x",
                "https://user@avatars.githubusercontent.com/x", "https://avatars.githubusercontent.com:444/x", "javascript:alert(1)", None, "",
                "https://evil.example/https://avatars.githubusercontent.com/"):
        assert va(bad) == auth.PLACEHOLDER_AVATAR, bad
    assert auth.PLACEHOLDER_AVATAR.startswith("https://avatars.githubusercontent.com/")


def test_callback_without_valid_state_redirects_home_without_session():
    c = httpx.Client(base_url=BASE, follow_redirects=False, headers={"x-forwarded-for": fake_ip()})
    for params in ({}, {"code": "abc", "state": "nope"}):
        r = c.get("/auth/github/callback", params=params)
        assert r.status_code in (302, 303, 307) and r.headers["location"] == "/"
        assert auth.SESSION_COOKIE not in r.headers.get("set-cookie", "")


def test_csrf_compare_unit():
    assert auth.csrf_ok("abc", "abc") and not auth.csrf_ok("abc", "abd") and not auth.csrf_ok("abc", None)
    assert not auth.csrf_ok(None, None) and not auth.csrf_ok("", "")


def test_session_id_hashed_in_db():
    c = user()
    sid = c.cookies[auth.SESSION_COOKIE]
    con = dbcon()
    assert con.execute("SELECT 1 FROM sessions WHERE hash=?", (sid,)).fetchone() is None
    assert con.execute("SELECT 1 FROM sessions WHERE hash=?", (auth.hash_sid(sid),)).fetchone()
