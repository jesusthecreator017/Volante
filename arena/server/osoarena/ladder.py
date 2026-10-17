"""Elo ladder, model state changes and pairing (PROMPT.md section 6). All functions take an open connection; callers wrap
writes in db.tx."""
import json
import random

from osoarena import config, db
from osoarena.text import clean, dumps, valid_build


def expected(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def k_factor(games):
    return 32 if games < config.get().provisional_games else 16


def elo(ra, rb, ga, gb, a_won):
    ea = expected(ra, rb)
    sa = 1.0 if a_won else 0.0
    da = k_factor(ga) * (sa - ea)
    db_ = k_factor(gb) * ((1.0 - sa) - (1.0 - ea))
    return round(da, 2), round(db_, 2)


# ---------------------------------------------------------------- model state

def cancel_queued_matches(con, model_id):
    """A retired/hidden model plays no new games: drop its matches that have not started (not tournament ones)."""
    ids = [r["id"] for r in con.execute(
        "SELECT id FROM matches WHERE status='queued' AND kind!='tournament' AND (a_id=? OR b_id=?)", (model_id, model_id))]
    for mid in ids:
        con.execute("DELETE FROM jobs WHERE kind='match' AND ref_id=? AND status='queued'", (mid,))
        con.execute("DELETE FROM matches WHERE id=?", (mid,))


def retire(con, model_id):
    con.execute("UPDATE models SET status='retired', retired_at=? WHERE id=? AND status='active'", (db.now(), model_id))
    cancel_queued_matches(con, model_id)


def duplicate_reason(con, season_id, sha, model_id=None):
    base = db.base_model_info()
    if base and sha == base["sha256"]:
        return "this file is identical to the base model; train it further before uploading"
    r = con.execute("SELECT id FROM models WHERE season_id=? AND sha256=? AND status='active' AND id IS NOT ?",
                    (season_id, sha, model_id)).fetchone()
    if r:
        return "this file is identical to a model that is already on the ladder"
    return None


def apply_check(con, model_id, result):
    """result: the child's result dict, or None when the child did not finish (timeout/kill)."""
    m = con.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m or m["status"] != "pending":
        return
    if result is None:
        return _reject(con, m, ["the check did not finish in time"], {})
    if "error" in result or not isinstance(result.get("reasons"), list):
        return _reject(con, m, ["the check could not be completed"], {})
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    reasons = [clean(str(x), 500) or "invalid file" for x in result["reasons"][:20]]
    if not result.get("ok") or reasons:
        return _reject(con, m, reasons or ["invalid file"], meta)
    name, build = clean(meta.get("name"), 40), valid_build(meta.get("build"))
    desc = clean(meta.get("description"), 280, allow_newline=True)
    if meta.get("sha256") != m["sha256"] or not name or not build:
        return _reject(con, m, ["the check could not be completed"], meta)
    dup = duplicate_reason(con, m["season_id"], m["sha256"], m["id"])
    if dup:
        return _reject(con, m, [dup], meta)
    for old in con.execute("SELECT id FROM models WHERE season_id=? AND user_id=? AND status='active' AND id!=?",
                           (m["season_id"], m["user_id"], m["id"])).fetchall():
        retire(con, old["id"])
    con.execute("UPDATE models SET status='active', name=?, description=?, build=?, rating=1200, games=0 WHERE id=?",
                (name, desc, dumps(build), m["id"]))


def _reject(con, m, reasons, meta):
    name = clean(meta.get("name"), 40) or "(rejected upload)"
    build = valid_build(meta.get("build")) or db.PLACEHOLDER_BUILD
    con.execute("UPDATE models SET status='rejected', name=?, build=?, rating=NULL, reject_reasons=? WHERE id=?",
                (name, dumps(build), dumps(reasons), m["id"]))


# ---------------------------------------------------------------- match results

def apply_match_result(con, match, res):
    """res: validated child result for a finished (not errored) match."""
    t = db.now()
    games = [[int(g[0]), int(g[1])] for g in res["games"]][:3]
    won = [int(res["score"][0]), int(res["score"][1])]
    if res["voided"]:
        con.execute("UPDATE matches SET status='void', games_won=?, games=?, winner=NULL, rating_delta=NULL, rallies='[]', "
                    "duration_s=?, reason=?, finished_at=? WHERE id=?",
                    (dumps(won), dumps(games), res["duration_s"], clean(str(res.get("reason") or "void"), 200), t, match["id"]))
        return
    winner = "ab"[int(res["winner"])]
    delta = None
    if match["kind"] != "tournament":
        a = con.execute("SELECT * FROM models WHERE id=?", (match["a_id"],)).fetchone()
        b = con.execute("SELECT * FROM models WHERE id=?", (match["b_id"],)).fetchone()
        da, dbb = elo(a["rating"] or 1200, b["rating"] or 1200, a["games"], b["games"], winner == "a")
        delta = {"a": da, "b": dbb}
        for row, d, w in ((a, da, winner == "a"), (b, dbb, winner == "b")):
            r = round((row["rating"] or 1200) + d, 2)
            con.execute("UPDATE models SET rating=?, games=games+1, wins=wins+?, losses=losses+? WHERE id=?",
                        (r, int(w), int(not w), row["id"]))
            con.execute("INSERT INTO rating_history (model_id, t, rating, match_id) VALUES (?, ?, ?, ?)", (row["id"], t, r, match["id"]))
    con.execute("UPDATE matches SET status='done', games_won=?, games=?, winner=?, rating_delta=?, rallies=?, duration_s=?, "
                "finished_at=? WHERE id=?",
                (dumps(won), dumps(games), winner, dumps(delta) if delta else None, dumps(res["rallies"]), res["duration_s"], t,
                 match["id"]))


def note_match_error(con, match):
    """A crashed/killed child: retry up to 2 times, then `error`; after 3 errors for a pair, hide the pairing and flag both."""
    errors = match["errors"] + 1
    if errors <= 2:
        con.execute("UPDATE matches SET status='queued', errors=?, started_at=NULL WHERE id=?", (errors, match["id"]))
        db.enqueue(con, "match", match["id"])
        return
    con.execute("UPDATE matches SET status='error', errors=?, finished_at=? WHERE id=?", (errors, db.now(), match["id"]))
    lo, hi = sorted((match["a_id"], match["b_id"]))
    con.execute("INSERT INTO pair_errors (lo, hi, errors) VALUES (?, ?, 1) ON CONFLICT(lo, hi) DO UPDATE SET errors=errors+1", (lo, hi))
    if con.execute("SELECT errors FROM pair_errors WHERE lo=? AND hi=?", (lo, hi)).fetchone()["errors"] >= 3:
        con.execute("UPDATE models SET flagged=1 WHERE id IN (?, ?)", (lo, hi))


# ---------------------------------------------------------------- pairing

def owner_key(m):
    return "house" if m["house"] else f"u{m['user_id']}"


def create_match(con, season_id, kind, x, y, tournament_id=None, rng=random):
    a, b = (x, y) if rng.random() < 0.5 else (y, x)
    seed = rng.randrange(1, 2 ** 31)
    cur = con.execute("INSERT INTO matches (season_id, kind, a_id, b_id, seed, status, tournament_id, created_at) "
                      "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)", (season_id, kind, a, b, seed, tournament_id, db.now()))
    db.enqueue(con, "match", cur.lastrowid)
    return cur.lastrowid


def pair(con, rng=random):
    """One scheduler tick of ladder/placement pairing. Returns the number of matches created."""
    cfg = config.get()
    season = db.active_season(con)
    queued = con.execute("SELECT COUNT(*) c FROM matches WHERE status IN ('queued','running') AND kind!='tournament'").fetchone()["c"]
    room = cfg.match_queue_max - queued
    if room <= 0:
        return 0
    busy = {r["id"] for r in con.execute(
        "SELECT a_id id FROM matches WHERE status IN ('queued','running') UNION SELECT b_id FROM matches WHERE status IN ('queued','running')")}
    cutoff = db.fmt_t(db.parse_t(db.now()) - __import__("datetime").timedelta(seconds=cfg.match_cooldown_s))
    models = [m for m in con.execute("SELECT * FROM models WHERE season_id=? AND status='active'", (season["id"],)) if m["id"] not in busy]
    last = {}
    for m in models:
        r = con.execute("SELECT a_id, b_id, finished_at, created_at FROM matches WHERE (a_id=? OR b_id=?) AND kind!='tournament' "
                        "ORDER BY id DESC LIMIT 1", (m["id"], m["id"])).fetchone()
        last[m["id"]] = r
    ready = [m for m in models if not last[m["id"]] or (last[m["id"]]["finished_at"] or last[m["id"]]["created_at"]) <= cutoff]
    bad = {(r["lo"], r["hi"]) for r in con.execute("SELECT lo, hi FROM pair_errors WHERE errors>=3")}
    ready.sort(key=lambda m: (m["games"], rng.random()))
    taken, made = set(), 0
    for m in ready:
        if made >= room:
            break
        if m["id"] in taken:
            continue
        prev = last[m["id"]]
        prev_opp = None if not prev else (prev["b_id"] if prev["a_id"] == m["id"] else prev["a_id"])
        cands = [o for o in ready if o["id"] != m["id"] and o["id"] not in taken and owner_key(o) != owner_key(m)
                 and o["id"] != prev_opp and tuple(sorted((m["id"], o["id"]))) not in bad]
        ra = m["rating"] or 1200
        near = sorted(cands, key=lambda o: abs((o["rating"] or 1200) - ra))
        placement = m["games"] < cfg.placement_games
        in_range = [o for o in near if abs((o["rating"] or 1200) - ra) <= cfg.pair_range][:5]
        # a model still in placement takes its nearest opponent even outside +-200 (else a lone new model never places)
        pool = in_range or (near[:1] if placement else [])
        if not pool:
            continue
        o = rng.choice(pool)
        kind = "placement" if placement or o["games"] < cfg.placement_games else "ladder"
        create_match(con, season["id"], kind, m["id"], o["id"], rng=rng)
        taken |= {m["id"], o["id"]}
        made += 1
    return made


def model_record(con, model_id):
    return con.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()


def build_of(row):
    try:
        return json.loads(row["build"])
    except (TypeError, ValueError):
        return dict(db.PLACEHOLDER_BUILD)
