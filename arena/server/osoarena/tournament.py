"""Weekly top-8 single elimination (PROMPT.md section 6). rounds JSON: [[{seed_a, seed_b, a, b, match_id, winner}]] with model ids;
the API expands a/b to ModelSummary."""
import json
import os
from datetime import timedelta
from zoneinfo import ZoneInfo

from osoarena import config, db, ladder
from osoarena.text import dumps

ORDER = [(0, 7), (3, 4), (2, 5), (1, 6)]  # 1v8, 4v5, 3v6, 2v7


def local_tz():
    """The "server time" zone: $TZ (set in the units' env file), else the Pi's zone."""
    for name in (os.environ.get("TZ", "").lstrip(":"), _system_zone()):
        if name:
            try:
                return ZoneInfo(name)
            except (KeyError, ValueError):
                pass
    return ZoneInfo("UTC")


def _system_zone():
    try:
        return open("/etc/timezone").read().strip()
    except OSError:
        return ""


def next_start(after_utc):
    cfg = config.get()
    tz = local_tz()
    loc = after_utc.astimezone(tz)
    d = loc.replace(hour=cfg.tournament_hour, minute=0, second=0, microsecond=0)
    d += timedelta(days=(cfg.tournament_weekday - d.weekday()) % 7)
    if d <= loc:
        d += timedelta(days=7)
    return d


def ensure_scheduled(con):
    season = db.active_season(con)
    if con.execute("SELECT 1 FROM tournaments WHERE season_id=? AND status IN ('scheduled','running')", (season["id"],)).fetchone():
        return
    start = next_start(db.parse_t(db.now()))
    con.execute("INSERT INTO tournaments (season_id, starts_at, status) VALUES (?, ?, 'scheduled')", (season["id"], db.fmt_t(start)))


def entrants(con, season_id):
    cfg = config.get()
    return con.execute("SELECT * FROM models WHERE season_id=? AND status='active' AND house IS NULL AND games>=? "
                       "ORDER BY rating DESC, id ASC LIMIT ?", (season_id, cfg.tournament_min_games, cfg.tournament_size)).fetchall()


def draw(con, t):
    """At starts_at: seed the top 8 and create the quarterfinals, or skip the week (D6 default) when fewer than 8 qualify."""
    top = entrants(con, t["season_id"])
    if len(top) < config.get().tournament_size:
        con.execute("UPDATE tournaments SET status='skipped', note=? WHERE id=?",
                    (f"skipped: {len(top)} qualifying models (8 needed)", t["id"]))
        print(f"tournament {t['id']} skipped: only {len(top)} qualifying models", flush=True)
        return
    qf = []
    for i, j in ORDER:
        mid = ladder.create_match(con, t["season_id"], "tournament", top[i]["id"], top[j]["id"], tournament_id=t["id"])
        qf.append(dict(seed_a=i + 1, seed_b=j + 1, a=top[i]["id"], b=top[j]["id"], match_id=mid, winner=None))
    blank = lambda n: [dict(seed_a=None, seed_b=None, a=None, b=None, match_id=None, winner=None) for _ in range(n)]  # noqa: E731
    con.execute("UPDATE tournaments SET status='running', rounds=? WHERE id=?", (dumps([qf, blank(2), blank(1)]), t["id"]))


def _tie_winner(con, tie):
    """'a'/'b' once the tie's match is done; a void or errored match is replayed with a new seed."""
    m = con.execute("SELECT * FROM matches WHERE id=?", (tie["match_id"],)).fetchone()
    if not m or m["status"] in ("queued", "running"):
        return None
    if m["status"] == "done":
        side = m["winner"]  # match side
        winner_model = m["a_id"] if side == "a" else m["b_id"]
        return "a" if winner_model == tie["a"] else "b"
    tie["match_id"] = ladder.create_match(con, m["season_id"], "tournament", tie["a"], tie["b"], tournament_id=m["tournament_id"])
    return None


def advance(con):
    now = db.now()
    for t in con.execute("SELECT * FROM tournaments WHERE status='scheduled' AND starts_at<=?", (now,)).fetchall():
        draw(con, t)
    for t in con.execute("SELECT * FROM tournaments WHERE status='running'").fetchall():
        rounds = json.loads(t["rounds"])
        changed = False
        for r, ties in enumerate(rounds):
            for k, tie in enumerate(ties):
                if tie["match_id"] is not None and tie["winner"] is None:
                    before = tie["match_id"]
                    w = _tie_winner(con, tie)
                    changed |= tie["match_id"] != before
                    if w:
                        tie["winner"], changed = w, True
                if tie["winner"] and r + 1 < len(rounds):
                    nxt = rounds[r + 1][k // 2]
                    slot = "a" if k % 2 == 0 else "b"
                    if nxt[slot] is None:
                        nxt[slot] = tie[tie["winner"]]
                        nxt["seed_" + slot] = tie["seed_" + tie["winner"]]
                        changed = True
            if r + 1 < len(rounds):
                for nxt in rounds[r + 1]:
                    if nxt["a"] is not None and nxt["b"] is not None and nxt["match_id"] is None:
                        nxt["match_id"] = ladder.create_match(con, t["season_id"], "tournament", nxt["a"], nxt["b"], tournament_id=t["id"])
                        changed = True
        final = rounds[-1][0] if rounds else None
        status = "done" if final and final["winner"] else "running"
        if changed or status != t["status"]:
            con.execute("UPDATE tournaments SET rounds=?, status=? WHERE id=?", (dumps(rounds), status, t["id"]))


def run_now(con):
    """Admin: start a tournament immediately (draw now)."""
    season = db.active_season(con)
    con.execute("DELETE FROM tournaments WHERE season_id=? AND status='scheduled'", (season["id"],))
    cur = con.execute("INSERT INTO tournaments (season_id, starts_at, status) VALUES (?, ?, 'scheduled')", (season["id"], db.now()))
    t = con.execute("SELECT * FROM tournaments WHERE id=?", (cur.lastrowid,)).fetchone()
    draw(con, t)
    return con.execute("SELECT * FROM tournaments WHERE id=?", (cur.lastrowid,)).fetchone()
