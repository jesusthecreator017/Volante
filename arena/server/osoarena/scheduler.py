"""Scheduler (exactly one instance): every minute pairs ladder/placement matches, schedules/draws/advances the weekly tournament,
evicts the replay cache, and cleans up. Writes only DB rows and deletes cache files; runs no model code."""
import os
import signal
import sys
import time
from datetime import timedelta

from osoarena import config, db, ladder, replays, tournament

_stop = False


def tick(con):
    with db.tx(con):
        tournament.ensure_scheduled(con)
        tournament.recheck_twins(con)
        tournament.advance(con)
        ladder.queue_fidelity(con)  # first fidelity check of candidates that reached their 10 matches
    with db.tx(con):
        made = ladder.pair(con)
    replays.evict(con)
    housekeeping(con)
    return made


def housekeeping(con):
    t = db.now()
    old = db.fmt_t(db.parse_t(t) - timedelta(days=7))
    with db.tx(con):
        con.execute("DELETE FROM sessions WHERE expires_at<?", (t,))
        con.execute("DELETE FROM jobs WHERE status IN ('done','failed') AND finished_at<?", (old,))
    q = config.get().quarantine_dir
    if q.is_dir():
        for p in q.iterdir():  # stale partial uploads
            try:
                if time.time() - p.stat().st_mtime > 3600:
                    p.unlink()
            except OSError:
                pass


def main(argv):
    con = db.connect()
    db.init(con)

    def stop(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    interval = int(os.environ.get("OSO_ARENA_TICK_S", "60"))
    print(f"scheduler up, tick {interval}s, tz {tournament.local_tz()}", flush=True)
    while not _stop:
        try:
            made = tick(con)
            if made:
                print(f"paired {made} matches", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"tick failed: {type(e).__name__}: {e}", flush=True)
        for _ in range(interval * 2):
            if _stop:
                break
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
