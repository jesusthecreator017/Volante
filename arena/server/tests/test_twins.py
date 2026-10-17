"""Volante Twins on the server (PROMPT.md section 14 acceptance): profile storage, the fidelity gate, the separate Twins ladder,
the weekly re-check, pairing, tournaments and the API fields. In-process on a throwaway data dir; the sandboxed fidelity job's
child code runs directly on two real kit matches. Run:  python -m pytest -q tests/test_twins.py
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["OSO_ARENA_DATA_DIR"] = tempfile.mkdtemp(prefix="arena-twins-")
os.environ.setdefault("OSO_ARENA_SESSION_SECRET", "test-secret-" + "x" * 40)
os.environ["OSO_ARENA_COOKIE_SECURE"] = "0"
os.environ["OSO_ARENA_RATE_READS_PER_MIN"] = "1000"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from osoarena import child, config, db, ladder, supervisor, tournament, web  # noqa: E402

PROFILE_TEXT = json.dumps(json.loads((ROOT.parent / "kit/tests/data/twins_vector.profile.json").read_text()), separators=(",", ":"))
GOOD = {"build_ok": True, "tempo_ratio": 1.02, "tempo_ok": True, "model_median_gap_s": 2.04, "profile_median_gap_s": 2.0, "gaps": 40,
        "mix_js": None, "fidelity_ok": True, "reasons": []}
BAD = dict(GOOD, tempo_ratio=0.5, tempo_ok=False, fidelity_ok=False, reasons=["tempo ratio 0.50 is outside 0.75-1.33"])


def con_():
    con = db.connect()
    db.init(con)
    return con


def add_user(con, uid, login):
    con.execute("INSERT OR IGNORE INTO users (id, login, avatar_url, created_at) VALUES (?, ?, '', ?)", (uid, login, db.now()))


def upload(con, uid, name, profile=PROFILE_TEXT, build=None):
    """A pending model + the check result the sandbox would return (meta carries the header's style_profile)."""
    from oso_arena import style
    build = build or (style.build_from_profile(json.loads(PROFILE_TEXT)) if profile else {"power": 6, "speed": 6, "reach": 6, "accuracy": 6})
    sha = f"{name}-{random.random()}"
    with db.tx(con):
        add_user(con, uid, f"u{uid}")
        mid = con.execute("INSERT INTO models (season_id, user_id, sha256, name, build, status, created_at, uploaded_by) "
                          "VALUES (?, ?, ?, '(checking)', ?, 'pending', ?, ?)",
                          (db.active_season(con)["id"], uid, sha, json.dumps(db.PLACEHOLDER_BUILD), db.now(), uid)).lastrowid
        meta = {"name": name, "build": build, "sha256": sha}
        if profile is not None:
            meta["style_profile"] = profile
        ladder.apply_check(con, mid, {"ok": True, "reasons": [], "meta": meta})
    return mid


def finish_matches(con, mid, n, lad="open"):
    bot = con.execute("SELECT id FROM models WHERE house='bot'").fetchone()["id"]
    with db.tx(con):
        for i in range(n):
            mt = con.execute("SELECT * FROM matches WHERE id=?", (ladder.create_match(con, 1, "ladder", mid, bot, ladder=lad),)).fetchone()
            ladder.apply_match_result(con, mt, {"voided": False, "winner": i % 2, "score": [2, 1], "games": [[21, 18]], "duration_s": 60.0,
                                                "rallies": []})


def model(con, mid):
    return con.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()


def test_profile_storage_and_reject():
    con = con_()
    mid = upload(con, 9001, "Twin A")
    m = model(con, mid)
    assert m["status"] == "active" and m["ladder"] == "open" and json.loads(m["style_profile"]) == json.loads(PROFILE_TEXT)
    bad = upload(con, 9002, "Bad Twin", profile='{"schema":"volante-style/1"}')
    m = model(con, bad)
    assert m["status"] == "rejected" and json.loads(m["reject_reasons"])[0].startswith("header style_profile:")
    plain = upload(con, 9003, "Plain", profile=None)
    assert model(con, plain)["style_profile"] is None
    s = web.model_summary(con, model(con, plain))
    assert s["ladder"] == "open" and s["twin"] is None
    s = web.model_summary(con, model(con, mid))
    assert s["twin"]["profile"] == json.loads(PROFILE_TEXT) and s["twin"]["fidelity"] == {"ok": False, "tempo_ratio": None, "mix_js": None,
                                                                                          "checked_at": None}


def test_gate_recheck_and_api():
    con = con_()
    mid = upload(con, 9101, "Twin B")
    other = upload(con, 9102, "Open Player", profile=None)
    # fewer than 10 matches: no fidelity job, never on the twins ladder
    finish_matches(con, mid, 9)
    with db.tx(con):
        assert ladder.queue_fidelity(con) == 0
    finish_matches(con, mid, 1)
    with db.tx(con):
        assert ladder.queue_fidelity(con) == 1
        assert ladder.queue_fidelity(con) == 0  # not twice while one is queued
    job = con.execute("SELECT * FROM jobs WHERE kind='fidelity' AND ref_id=?", (mid,)).fetchone()
    with db.tx(con):
        payload = supervisor.job_input(con, job)
    assert payload["kind"] == "fidelity" and len(payload["logs"]) == 10 and payload["profile"] == PROFILE_TEXT
    # a failing report keeps it on the open ladder, with the reasons
    with db.tx(con):
        ladder.apply_fidelity(con, mid, BAD)
    m = model(con, mid)
    assert m["ladder"] == "open"
    tw = web.model_summary(con, m)["twin"]["fidelity"]
    assert tw["ok"] is False and tw["tempo_ratio"] == 0.5 and tw["checked_at"] and tw["reasons"]
    # the weekly re-check passes: admitted with a fresh 1200 Twins rating
    with db.tx(con):
        assert ladder.queue_fidelity(con) == 0  # first checks only for unchecked models
        con.execute("DELETE FROM jobs WHERE kind='fidelity'")
        assert ladder.queue_fidelity(con, recheck=True) == 1
        ladder.apply_fidelity(con, mid, GOOD)
    m = model(con, mid)
    assert m["ladder"] == "twins" and m["twins_rating"] == 1200
    # twins ladder games keep their own record
    finish_matches(con, mid, 3, lad="twins")
    m = model(con, mid)
    assert m["twins_games"] == 3 and m["games"] == 10
    frozen = m["twins_rating"]
    with TestClient(web.create_app()) as c:
        tw = c.get("/api/ladder?ladder=twins").json()
        names = [i["model"]["name"] for i in tw["items"]]
        assert "Twin B" in names and "Open Player" not in names and tw["ladder"] == "twins"
        assert all(i["model"]["ladder"] == "twins" or i["model"]["owner"].get("house") for i in tw["items"])
        op = c.get("/api/ladder").json()
        assert "Twin B" not in [i["model"]["name"] for i in op["items"]] and "Open Player" in [i["model"]["name"] for i in op["items"]]
        assert c.get("/api/ladder?ladder=nope").status_code == 400
        d = c.get(f"/api/models/{mid}").json()
        assert d["ladder"] == "twins" and d["twin"]["fidelity"]["ok"] is True and d["games"] == 3
    # a failing re-check moves it back to the open ladder; the Twins rating stays frozen and returns with it
    with db.tx(con):
        ladder.apply_fidelity(con, mid, BAD)
    m = model(con, mid)
    assert m["ladder"] == "open" and m["twins_rating"] == frozen
    with db.tx(con):
        ladder.apply_fidelity(con, mid, GOOD)
    assert model(con, mid)["twins_rating"] == frozen
    assert other


def test_pairing_keeps_ladders_apart():
    con = con_()
    with db.tx(con):
        con.execute("UPDATE models SET status='retired' WHERE house IS NULL")
    twin = upload(con, 9201, "Twin C")
    opn = upload(con, 9202, "Open C", profile=None)
    with db.tx(con):
        ladder.apply_fidelity(con, twin, GOOD)
        con.execute("DELETE FROM matches")
        con.execute("DELETE FROM jobs")
        made = ladder.pair(con, rng=random.Random(3))
    rows = con.execute("SELECT * FROM matches").fetchall()
    assert made == len(rows) >= 1
    house = {r["id"] for r in con.execute("SELECT id FROM models WHERE house IS NOT NULL")}
    for r in rows:
        ids = {r["a_id"], r["b_id"]}
        assert not (twin in ids and opn in ids)  # twins never play open models
        if twin in ids:
            assert r["ladder"] == "twins" and ids - {twin} <= house
        if opn in ids:
            assert r["ladder"] == "open"


def test_twins_tournament_series():
    con = con_()
    with db.tx(con):
        con.execute("DELETE FROM tournaments")
        tournament.ensure_scheduled(con)
    rows = {r["ladder"]: r for r in con.execute("SELECT * FROM tournaments WHERE status='scheduled'")}
    assert set(rows) == {"open", "twins"}
    tz = tournament.local_tz()
    t = db.parse_t(rows["twins"]["starts_at"]).astimezone(tz)
    assert (t.weekday(), t.hour) == (config.get().twins_tournament_weekday, config.get().twins_tournament_hour)
    with TestClient(web.create_app()) as c:
        assert c.get("/api/tournaments/current?ladder=twins").json()["ladder"] == "twins"
        assert all(x["ladder"] == "open" for x in c.get("/api/tournaments").json())
        assert all(x["ladder"] == "twins" for x in c.get("/api/tournaments?ladder=twins").json())
    # the re-check is queued once, twins_recheck_lead_h before the start
    with db.tx(con):
        con.execute("UPDATE tournaments SET starts_at=? WHERE id=?", (db.now(), rows["twins"]["id"]))
        tournament.recheck_twins(con)
        assert db.get_meta(con, f"twins_recheck:{rows['twins']['id']}")


def test_fidelity_child_on_real_matches(tmp_path):
    """The sandbox side: real kit matches -> logs -> child.do_fidelity -> the supervisor's validation."""
    from oso_arena import log as L, match, style
    prof = json.loads(PROFILE_TEXT)
    paths = []
    for seed in (1, 2):
        m = match.play("bot", "bot", seed, target=5, best_of=1)
        p = tmp_path / f"{seed}.osml.gz"
        L.write(p, m)
        paths.append([str(p), seed % 2])
    res = child.do_fidelity({"profile": PROFILE_TEXT, "build": style.build_from_profile(prof), "logs": paths}, str(tmp_path))
    assert res["logs"] == 2 and supervisor.valid_fidelity_result(res)
    assert set(res["report"]) >= {"build_ok", "tempo_ratio", "fidelity_ok", "reasons"} and res["report"]["build_ok"] is True
    assert not supervisor.valid_fidelity_result({"ok": True, "report": dict(res["report"], fidelity_ok="yes")})
