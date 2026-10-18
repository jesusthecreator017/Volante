"""SQLite (WAL) schema and helpers. Used only by web, scheduler, supervisors and the admin CLI, never by a sandboxed child."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from osoarena import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS seasons (
  id INTEGER PRIMARY KEY, contract TEXT NOT NULL, kit_version TEXT NOT NULL,
  started_at TEXT NOT NULL, ended_at TEXT, active INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,              -- GitHub user id; 0 = the "deleted" placeholder
  login TEXT NOT NULL, avatar_url TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pods (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  hw_id TEXT NOT NULL UNIQUE,          -- e.g. OSO-4C99; one account per pod
  name TEXT NOT NULL, last_seen TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pods_user ON pods(user_id);
CREATE TABLE IF NOT EXISTS app_sessions (
  user_id INTEGER NOT NULL REFERENCES users(id), id TEXT NOT NULL, started_at TEXT NOT NULL, source TEXT NOT NULL,
  report TEXT NOT NULL, received_at TEXT NOT NULL, PRIMARY KEY (user_id, id));
CREATE TABLE IF NOT EXISTS app_style (
  user_id INTEGER PRIMARY KEY REFERENCES users(id), profile TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_codes (
  hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, challenge TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bans (github_id INTEGER PRIMARY KEY, t TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), csrf TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS models (
  id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL REFERENCES seasons(id),
  user_id INTEGER REFERENCES users(id),  -- NULL for house players
  house TEXT,                            -- NULL, 'bot' or 'base'
  sha256 TEXT, name TEXT NOT NULL, description TEXT, build TEXT NOT NULL,
  status TEXT NOT NULL, rating REAL, games INTEGER NOT NULL DEFAULT 0,
  wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, retired_at TEXT, reject_reasons TEXT, uploaded_by INTEGER,
  status_before_hide TEXT, flagged INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS models_season ON models(season_id, status);
CREATE INDEX IF NOT EXISTS models_user ON models(user_id);
CREATE INDEX IF NOT EXISTS models_sha ON models(sha256);
CREATE INDEX IF NOT EXISTS models_upl ON models(uploaded_by, created_at);
CREATE TABLE IF NOT EXISTS rating_history (
  model_id INTEGER NOT NULL, t TEXT NOT NULL, rating REAL NOT NULL, match_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS rh_model ON rating_history(model_id);
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, kind TEXT NOT NULL,
  a_id INTEGER NOT NULL, b_id INTEGER NOT NULL, seed INTEGER NOT NULL, status TEXT NOT NULL,
  games_won TEXT, winner TEXT, games TEXT, rating_delta TEXT, rallies TEXT,
  duration_s REAL, reason TEXT, errors INTEGER NOT NULL DEFAULT 0, tournament_id INTEGER,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS matches_season ON matches(season_id, status);
CREATE INDEX IF NOT EXISTS matches_a ON matches(a_id);
CREATE INDEX IF NOT EXISTS matches_b ON matches(b_id);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref_id INTEGER NOT NULL, priority INTEGER NOT NULL,
  status TEXT NOT NULL, worker TEXT, error TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, cpu_s REAL, wall_s REAL);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, priority, id);
CREATE TABLE IF NOT EXISTS replays (
  match_id INTEGER PRIMARY KEY, status TEXT NOT NULL, size INTEGER, raw_size INTEGER,
  detail TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_access TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tournaments (
  id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, starts_at TEXT NOT NULL,
  status TEXT NOT NULL, rounds TEXT NOT NULL DEFAULT '[]', note TEXT);
CREATE TABLE IF NOT EXISTS pair_errors (
  lo INTEGER NOT NULL, hi INTEGER NOT NULL, errors INTEGER NOT NULL, PRIMARY KEY (lo, hi));
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

PLACEHOLDER_BUILD = {"power": 6, "speed": 6, "reach": 6, "accuracy": 6}
DELETED_USER = {"id": 0, "login": "deleted", "avatar_url": "https://avatars.githubusercontent.com/u/0?s=64"}


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_t(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_t(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path=None):
    path = Path(path or config.get().db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")  # before journal_mode: switching to WAL takes a lock other starters contend for
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


class tx:
    """with tx(con): ... -- BEGIN IMMEDIATE / COMMIT (ROLLBACK on error)."""

    def __init__(self, con):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, et, ev, tb):
        self.con.execute("ROLLBACK" if et else "COMMIT")
        return False


# Volante Twins (PROMPT.md section 14): additive columns, added to existing databases on start.
#   models.ladder 'open' | 'twins'; style_profile = the header's JSON text; fidelity = the last FidelityReport.to_dict() (JSON),
#   fidelity_at its time; twins_* = the separate Twins ladder record (kept, frozen, while the model is back on the open ladder;
#   the house players have one too). matches / rating_history / tournaments.ladder say which ladder they belong to.
COLUMNS = {
    "users": [("app_linked_at", "TEXT")],
    "models": [("ladder", "TEXT NOT NULL DEFAULT 'open'"), ("style_profile", "TEXT"), ("fidelity", "TEXT"), ("fidelity_at", "TEXT"),
               ("twins_rating", "REAL"), ("twins_games", "INTEGER NOT NULL DEFAULT 0"), ("twins_wins", "INTEGER NOT NULL DEFAULT 0"),
               ("twins_losses", "INTEGER NOT NULL DEFAULT 0")],
    "matches": [("ladder", "TEXT NOT NULL DEFAULT 'open'")],
    "rating_history": [("ladder", "TEXT NOT NULL DEFAULT 'open'")],
    "tournaments": [("ladder", "TEXT NOT NULL DEFAULT 'open'")],
}

LADDERS = ("open", "twins")


def migrate(con):
    for table, cols in COLUMNS.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    con.execute("CREATE INDEX IF NOT EXISTS models_ladder ON models(season_id, status, ladder)")


def init(con):
    from oso_arena import CONTRACT, KIT_VERSION
    con.executescript(SCHEMA)
    with tx(con):
        migrate(con)
    with tx(con):
        if not con.execute("SELECT 1 FROM seasons").fetchone():
            con.execute("INSERT INTO seasons (contract, kit_version, started_at, active) VALUES (?, ?, ?, 1)",
                        (CONTRACT, KIT_VERSION, now()))
        con.execute("INSERT OR IGNORE INTO users (id, login, avatar_url, created_at) VALUES (0, ?, ?, ?)",
                    (DELETED_USER["login"], DELETED_USER["avatar_url"], now()))
        sid = active_season(con)["id"]
        ensure_house(con, sid)


def ensure_house(con, season_id):
    """House Bot always; House Base only once the base model file is configured (MISSING_BASE_MODEL.txt: TODO until it arrives)."""
    if not con.execute("SELECT 1 FROM models WHERE season_id=? AND house='bot'", (season_id,)).fetchone():
        con.execute("INSERT INTO models (season_id, house, sha256, name, description, build, status, rating, created_at) "
                    "VALUES (?, 'bot', 'bot', 'House Bot', 'The scripted bot.', ?, 'active', 1200, ?)",
                    (season_id, json.dumps(PLACEHOLDER_BUILD), now()))
    base = base_model_info()
    if base and not con.execute("SELECT 1 FROM models WHERE season_id=? AND house='base'", (season_id,)).fetchone():
        con.execute("INSERT INTO models (season_id, house, sha256, name, description, build, status, rating, created_at) "
                    "VALUES (?, 'base', ?, 'House Base', 'The base model.', ?, 'active', 1200, ?)",
                    (season_id, base["sha256"], json.dumps(PLACEHOLDER_BUILD), now()))


def base_model_info():
    import hashlib
    from pathlib import Path
    p = config.get().base_model
    if not p or not Path(p).is_file():
        return None
    return {"path": p, "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()}


def active_season(con):
    return con.execute("SELECT * FROM seasons WHERE active=1").fetchone()


def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def set_meta(con, k, v):
    con.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def enqueue(con, kind, ref_id):
    prio = {"check": 0, "match": 1, "fidelity": 1, "replay": 2}[kind]
    cur = con.execute("INSERT INTO jobs (kind, ref_id, priority, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
                      (kind, ref_id, prio, now()))
    return cur.lastrowid
