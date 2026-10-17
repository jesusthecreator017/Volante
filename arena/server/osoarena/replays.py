"""Replay cache: one pre-gzipped replay.bin.gz per match, built on demand by a sandboxed job, LRU-bounded (5 GB) with tournament
finals and the latest 20 ladder matches pinned."""
import json

from osoarena import config, db


def request(con, match_id):
    """-> ('ready', path) | ('building', None) | ('failed', detail) | ('busy', None). Queues a build on first request."""
    cfg = config.get()
    with db.tx(con):
        r = con.execute("SELECT * FROM replays WHERE match_id=?", (match_id,)).fetchone()
        path = cfg.replays_dir / f"{match_id}.bin.gz"
        if r and r["status"] == "ready":
            if path.is_file():
                con.execute("UPDATE replays SET last_access=? WHERE match_id=?", (db.now(), match_id))
                return "ready", path
            con.execute("DELETE FROM replays WHERE match_id=?", (match_id,))
            r = None
        if r and r["status"] == "failed":
            return "failed", r["detail"] or "The replay could not be built."
        if r and r["status"] == "building":
            return "building", None
        pending = con.execute("SELECT COUNT(*) c FROM replays WHERE status='building'").fetchone()["c"]
        if pending >= cfg.replay_queue_max:
            return "busy", None
        if not (cfg.logs_dir / f"{match_id}.osml.gz").is_file():
            return "failed", "The match log is missing."
        con.execute("INSERT OR REPLACE INTO replays (match_id, status, attempts, created_at) VALUES (?, 'building', 0, ?)",
                    (match_id, db.now()))
        db.enqueue(con, "replay", match_id)
        return "building", None


def pinned(con):
    keep = {r["id"] for r in con.execute(
        "SELECT id FROM matches WHERE kind IN ('ladder','placement') AND status='done' ORDER BY id DESC LIMIT ?",
        (config.get().replay_keep_latest,))}
    for t in con.execute("SELECT rounds FROM tournaments WHERE status IN ('running','done')"):
        rounds = json.loads(t["rounds"])
        if rounds and rounds[-1] and rounds[-1][0].get("match_id"):
            keep.add(rounds[-1][0]["match_id"])
    return keep


def evict(con):
    cfg = config.get()
    rows = con.execute("SELECT match_id, size FROM replays WHERE status='ready' ORDER BY last_access ASC").fetchall()
    total = sum(r["size"] or 0 for r in rows)
    if total <= cfg.replay_cache_bytes:
        return 0
    keep, n = pinned(con), 0
    for r in rows:
        if total <= cfg.replay_cache_bytes:
            break
        if r["match_id"] in keep:
            continue
        try:
            (cfg.replays_dir / f"{r['match_id']}.bin.gz").unlink()
        except FileNotFoundError:
            pass
        with db.tx(con):
            con.execute("DELETE FROM replays WHERE match_id=?", (r["match_id"],))
        total -= r["size"] or 0
        n += 1
    return n
