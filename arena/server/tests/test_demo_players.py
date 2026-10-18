"""Demo players: seeded once per season from server/demo/, and the first tournament starts once 8 have qualified.

Run:  python -m pytest -q tests/test_demo_players.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["OSO_ARENA_DATA_DIR"] = tempfile.mkdtemp(prefix="arena-demo-")
os.environ.setdefault("OSO_ARENA_SESSION_SECRET", "test-secret-" + "x" * 40)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osoarena import config, db, scheduler  # noqa: E402


def test_demo_players_and_first_tournament():
    con = db.connect()
    con.executescript(db.SCHEMA)
    with db.tx(con):
        db.set_meta(con, "demo_seeded_1", "x")  # the old key, written by a deploy that had no demo folder
    db.init(con)
    db.init(con)  # idempotent
    demo = con.execute("SELECT * FROM models WHERE user_id>? AND status='active'", (db.DEMO_USER_BASE,)).fetchall()
    assert len(demo) == 8 and len({m["name"] for m in demo}) == 8
    for m in demo:
        assert (config.get().models_dir / f"{m['sha256']}.safetensors").is_file()
    with db.tx(con):
        scheduler.demo_tournament(con)  # nobody has played yet
    assert not con.execute("SELECT 1 FROM tournaments WHERE status='running'").fetchone()
    with db.tx(con):
        con.execute("UPDATE models SET games=? WHERE user_id>?", (config.get().tournament_min_games, db.DEMO_USER_BASE))
        scheduler.demo_tournament(con)
    assert con.execute("SELECT COUNT(*) FROM tournaments WHERE status='running'").fetchone()[0] == 1
    assert db.get_meta(con, "demo_tournament_pending") == "0"
