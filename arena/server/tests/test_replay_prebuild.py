"""A finished match queues its replay build at once (lowest priority), so the first viewer doesn't wait; a failed match doesn't.
In-process with the subprocess sandbox: `python -m pytest -q tests/test_replay_prebuild.py` (needs the kit installed)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["OSO_ARENA_DATA_DIR"] = tempfile.mkdtemp(prefix="arena-prebuild-")
os.environ.setdefault("OSO_ARENA_SESSION_SECRET", "test-secret-" + "x" * 40)
os.environ["OSO_ARENA_SANDBOX"] = "subprocess"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osoarena import config, db, ladder, supervisor  # noqa: E402


def _match(con):
    cfg = config.get()
    for d in (cfg.logs_dir, cfg.replays_dir, cfg.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    db.init(con)
    bot = con.execute("SELECT id FROM models WHERE house='bot'").fetchone()["id"]
    with db.tx(con):
        mid = ladder.create_match(con, db.active_season(con)["id"], "ladder", bot, bot)
        db.enqueue(con, "match", mid)
    return mid


def _claim(con, kind):
    job = supervisor.claim(con, "t")
    assert job is not None and job["kind"] == kind, job
    return job


def test_finished_match_queues_its_replay():
    con = db.connect()
    mid = _match(con)
    supervisor.process(con, _claim(con, "match"))
    assert con.execute("SELECT status FROM matches WHERE id=?", (mid,)).fetchone()["status"] == "done"
    assert con.execute("SELECT status FROM replays WHERE match_id=?", (mid,)).fetchone()["status"] == "building"
    job = con.execute("SELECT * FROM jobs WHERE kind='replay' AND ref_id=? AND status='queued'", (mid,)).fetchone()
    assert job is not None and job["priority"] == 2  # after every check and match
    supervisor.process(con, job)
    assert con.execute("SELECT status FROM replays WHERE match_id=?", (mid,)).fetchone()["status"] == "ready"


def test_failed_match_queues_nothing(monkeypatch):
    con = db.connect()
    mid = _match(con)
    monkeypatch.setattr(supervisor, "run_child", lambda job_id, payload: (None, 0.0))
    supervisor.process(con, _claim(con, "match"))
    assert con.execute("SELECT 1 FROM replays WHERE match_id=?", (mid,)).fetchone() is None
    assert con.execute("SELECT 1 FROM jobs WHERE kind='replay' AND ref_id=?", (mid,)).fetchone() is None
