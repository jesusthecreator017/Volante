"""Admin CLI (run on the Pi as the service user: `sudo -u oso-arena oso-arena-admin <command>`; the wrapper in /usr/local/bin does
that and loads the env file)."""
import argparse
import json
import shutil
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

from osoarena import config, db, ladder, tournament


def _con():
    con = db.connect()
    db.init(con)
    return con


def cmd_status(a):
    con = _con()
    s = db.active_season(con)
    print(f"season {s['id']} {s['contract']} kit {s['kit_version']} since {s['started_at']}")
    for r in con.execute("SELECT status, COUNT(*) c FROM models GROUP BY status"):
        print(f"  models {r['status']}: {r['c']}")
    for r in con.execute("SELECT kind, status, COUNT(*) c FROM jobs WHERE status IN ('queued','running') GROUP BY kind, status"):
        print(f"  jobs {r['kind']} {r['status']}: {r['c']}")
    for r in con.execute("SELECT k, v FROM meta WHERE k LIKE 'worker:%'"):
        print(f"  {r['k']} last seen {r['v']}")
    for r in con.execute("SELECT kind, COUNT(*) c, ROUND(AVG(wall_s),1) w, ROUND(AVG(cpu_s),1) cpu FROM jobs "
                         "WHERE status='done' AND finished_at > datetime('now','-1 hour') GROUP BY kind"):
        print(f"  last hour {r['kind']}: {r['c']} jobs, avg wall {r['w']} s, avg cpu {r['cpu']} s")
    n = con.execute("SELECT COUNT(*) c FROM matches WHERE status='done' AND finished_at > strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 hour')").fetchone()
    print(f"  matches finished in the last hour: {n['c']}")
    for r in con.execute("SELECT id, name, flagged FROM models WHERE flagged=1"):
        print(f"  FLAGGED model {r['id']}")
    t = con.execute("SELECT * FROM tournaments WHERE status IN ('scheduled','running') ORDER BY id DESC LIMIT 1").fetchone()
    if t:
        print(f"  tournament {t['id']} {t['status']} starts {t['starts_at']} (tz {tournament.local_tz()})")


def cmd_hide(a):
    con = _con()
    with db.tx(con):
        m = con.execute("SELECT * FROM models WHERE id=?", (a.model,)).fetchone()
        if not m or m["status"] == "hidden":
            sys.exit("no such model, or already hidden")
        con.execute("UPDATE models SET status='hidden', status_before_hide=? WHERE id=?", (m["status"], m["id"]))
        ladder.cancel_queued_matches(con, m["id"])
    print(f"model {a.model} hidden")


def cmd_unhide(a):
    con = _con()
    with db.tx(con):
        m = con.execute("SELECT * FROM models WHERE id=? AND status='hidden'", (a.model,)).fetchone()
        if not m:
            sys.exit("no such hidden model")
        prev = m["status_before_hide"] or "retired"
        if prev == "active" and con.execute("SELECT 1 FROM models WHERE season_id=? AND user_id IS ? AND status='active' AND house IS NULL",
                                            (m["season_id"], m["user_id"])).fetchone():
            prev = "retired"  # the owner has uploaded another active model since
        con.execute("UPDATE models SET status=?, status_before_hide=NULL WHERE id=?", (prev, m["id"]))
    print(f"model {a.model} -> {prev}")


def cmd_ban(a):
    con = _con()
    with db.tx(con):
        con.execute("INSERT OR REPLACE INTO bans (github_id, t, note) VALUES (?, ?, ?)", (a.user, db.now(), a.note))
        for m in con.execute("SELECT id FROM models WHERE user_id=? AND status='active'", (a.user,)).fetchall():
            ladder.retire(con, m["id"])
        con.execute("DELETE FROM sessions WHERE user_id=?", (a.user,))
    print(f"user {a.user} banned, models retired, sessions ended")


def cmd_unban(a):
    con = _con()
    with db.tx(con):
        con.execute("DELETE FROM bans WHERE github_id=?", (a.user,))
    print(f"user {a.user} unbanned")


def cmd_new_season(a):
    from oso_arena import CONTRACT, KIT_VERSION
    con = _con()
    contract, kit = a.contract or CONTRACT, a.kit or KIT_VERSION
    with db.tx(con):
        old = db.active_season(con)
        if old["contract"] == contract and old["kit_version"] == kit and not a.force:
            sys.exit(f"season {old['id']} already uses {contract} / {kit}; upgrade the kit first (or --force)")
        con.execute("UPDATE seasons SET active=0, ended_at=? WHERE id=?", (db.now(), old["id"]))
        for m in con.execute("SELECT id FROM models WHERE season_id=? AND status='active'", (old["id"],)).fetchall():
            ladder.cancel_queued_matches(con, m["id"])
        con.execute("UPDATE models SET status='retired', retired_at=? WHERE season_id=? AND status IN ('active','pending')",
                    (db.now(), old["id"]))
        con.execute("UPDATE tournaments SET status='skipped', note='season ended' WHERE season_id=? AND status='scheduled'", (old["id"],))
        cur = con.execute("INSERT INTO seasons (contract, kit_version, started_at, active) VALUES (?, ?, ?, 1)", (contract, kit, db.now()))
        db.ensure_house(con, cur.lastrowid)
    print(f"season {cur.lastrowid} started ({contract}, kit {kit}); users must re-submit")


def cmd_tournament_now(a):
    con = _con()
    with db.tx(con):
        t = tournament.run_now(con)
    print(f"tournament {t['id']}: {t['status']} {t['note'] or ''}")


def cmd_void(a):
    con = _con()
    with db.tx(con):
        mt = con.execute("SELECT * FROM matches WHERE id=?", (a.match,)).fetchone()
        if not mt:
            sys.exit("no such match")
        if mt["status"] == "done" and mt["rating_delta"]:
            d = json.loads(mt["rating_delta"])
            for side in ("a", "b"):
                mid = mt[f"{side}_id"]
                won = mt["winner"] == side
                con.execute("UPDATE models SET rating=rating-?, games=games-1, wins=wins-?, losses=losses-? WHERE id=?",
                            (d[side], int(won), int(not won), mid))
                con.execute("DELETE FROM rating_history WHERE model_id=? AND match_id=?", (mid, mt["id"]))
        con.execute("DELETE FROM jobs WHERE kind='match' AND ref_id=? AND status='queued'", (mt["id"],))
        con.execute("UPDATE matches SET status='void', winner=NULL, rating_delta=NULL, reason=?, finished_at=COALESCE(finished_at, ?) "
                    "WHERE id=?", (a.reason or "voided by the admin", db.now(), mt["id"]))
    print(f"match {a.match} voided (rating changes reverted; later matches are not recomputed)")


def cmd_rebuild_replay(a):
    con = _con()
    cfg = config.get()
    with db.tx(con):
        con.execute("DELETE FROM replays WHERE match_id=?", (a.match,))
    (cfg.replays_dir / f"{a.match}.bin.gz").unlink(missing_ok=True)
    from osoarena import replays
    print(replays.request(con, a.match)[0])


def cmd_export(a):
    con = _con()
    out = Path(a.path)
    if out.exists():
        sys.exit(f"{out} exists")
    con.execute("VACUUM INTO ?", (str(out),))
    print(f"exported {out}")


def cmd_backup(a):
    """VACUUM INTO a consistent DB copy + the model files, into <dir>/<timestamp>/; keeps the newest N."""
    cfg = config.get()
    con = _con()
    root = Path(a.dest or cfg.backup_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = root / stamp
    d.mkdir(parents=True)
    con.execute("VACUUM INTO ?", (str(d / "arena.sqlite3"),))
    with tarfile.open(d / "models.tar", "w") as tf:
        tf.add(cfg.models_dir, arcname="models")
    chk = sqlite3.connect(str(d / "arena.sqlite3")).execute("PRAGMA integrity_check").fetchone()[0]
    (d / "OK").write_text(chk + "\n")
    backups = sorted(p for p in root.iterdir() if p.is_dir())
    for old in backups[:-a.keep]:
        shutil.rmtree(old)
    print(f"backup {d} (integrity {chk})")


def cmd_restore(a):
    """Stop the services first (oso-arena-ctl stop). Replaces the DB and the model files from a backup directory."""
    cfg = config.get()
    src = Path(a.path)
    if not (src / "arena.sqlite3").is_file() or not (src / "models.tar").is_file():
        sys.exit("not a backup directory")
    for suf in ("", "-wal", "-shm"):
        p = Path(str(cfg.db_path) + suf)
        if p.exists():
            p.rename(p.with_name(p.name + f".before-restore-{int(time.time())}"))
    shutil.copyfile(src / "arena.sqlite3", cfg.db_path)
    with tarfile.open(src / "models.tar") as tf:
        for mem in tf.getmembers():
            if not mem.isfile() or not mem.name.startswith("models/") or "/" in mem.name[7:] or not mem.name.endswith(".safetensors"):
                continue
            dst = cfg.models_dir / mem.name[7:]
            if not dst.exists():
                with tf.extractfile(mem) as fi, open(dst, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                dst.chmod(0o640)
    print(f"restored from {src}; start the services again")


def cmd_set_base(a):
    print("Set OSO_ARENA_BASE_MODEL=<path> in /etc/oso-arena/arena.env and restart: House Base is created at start-up.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="oso-arena-admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(f=cmd_status)
    for name, f in (("hide", cmd_hide), ("unhide", cmd_unhide)):
        s = sub.add_parser(name)
        s.add_argument("model", type=int)
        s.set_defaults(f=f)
    s = sub.add_parser("ban")
    s.add_argument("user", type=int, help="GitHub user id")
    s.add_argument("--note", default="")
    s.set_defaults(f=cmd_ban)
    s = sub.add_parser("unban")
    s.add_argument("user", type=int)
    s.set_defaults(f=cmd_unban)
    s = sub.add_parser("new-season")
    s.add_argument("--contract")
    s.add_argument("--kit")
    s.add_argument("--force", action="store_true")
    s.set_defaults(f=cmd_new_season)
    sub.add_parser("tournament-now").set_defaults(f=cmd_tournament_now)
    s = sub.add_parser("void-match")
    s.add_argument("match", type=int)
    s.add_argument("--reason")
    s.set_defaults(f=cmd_void)
    s = sub.add_parser("rebuild-replay")
    s.add_argument("match", type=int)
    s.set_defaults(f=cmd_rebuild_replay)
    s = sub.add_parser("export-db")
    s.add_argument("path")
    s.set_defaults(f=cmd_export)
    s = sub.add_parser("backup")
    s.add_argument("--dest")
    s.add_argument("--keep", type=int, default=14)
    s.set_defaults(f=cmd_backup)
    s = sub.add_parser("restore")
    s.add_argument("path")
    s.set_defaults(f=cmd_restore)
    sub.add_parser("base-model").set_defaults(f=cmd_set_base)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
