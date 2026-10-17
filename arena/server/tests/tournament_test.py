"""Acceptance 11.4: upload 10 test models (tests/make_models.py), let them play until eligible (test config lowers the 20-game rule),
run a tournament now with the admin CLI, wait until it is done, check the bracket and that tournament matches change no rating.
Usage: python tests/tournament_test.py <base url> <models dir>   (env: the server's OSO_ARENA_* settings)"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from smoke_pipeline import client  # noqa: E402

from osoarena import admin, config  # noqa: E402


def main(base, models):
    cfg = config.get()
    files = sorted(Path(models).glob("*.safetensors"))
    ids = []
    for i, f in enumerate(files):
        c = client(base, 700000 + i, f"tourney-{i}")
        r = c.post("/api/models", files={"file": (f.name, f.read_bytes())})
        assert r.status_code == 202, r.text
        ids.append(r.json()["id"])
    anon = client(base, 699999, "watcher")
    deadline = time.time() + 900
    while time.time() < deadline:
        lad = anon.get("/api/ladder?limit=100").json()
        ready = [x for x in lad["items"] if x["model"]["id"] in ids and x["games"] >= cfg.tournament_min_games]
        if len(ready) >= 8:
            break
        time.sleep(3)
    print(f"{len(ready)} eligible models after {int(time.time() - deadline + 900)} s")
    before = {x["model"]["id"]: x["rating"] for x in anon.get("/api/ladder?limit=100").json()["items"]}
    admin.main(["tournament-now"])
    cur = anon.get("/api/tournaments/current").json()
    tid = cur["id"]
    assert cur["status"] == "running" and len(cur["rounds"]) == 3, cur
    qf = cur["rounds"][0]
    assert [(t["seed_a"], t["seed_b"]) for t in qf] == [(1, 8), (4, 5), (3, 6), (2, 7)], qf
    assert all(t["match_id"] for t in qf) and all(t["a"] is None and t["b"] is None for t in cur["rounds"][1] + cur["rounds"][2])
    while True:
        t = anon.get(f"/api/tournaments/{tid}").json()
        if t["status"] == "done":
            break
        time.sleep(3)
    for r, ties in enumerate(t["rounds"]):
        for tie in ties:
            m = anon.get(f"/api/matches/{tie['match_id']}").json()
            assert m["kind"] == "tournament" and m["rating_delta"] is None and m["status"] == "done", m
            winner_model = m["a"]["id"] if m["winner"] == "a" else m["b"]["id"]
            assert winner_model == tie[tie["winner"]]["id"], (tie, m["winner"])
            print(f"round {r + 1}: #{tie['seed_a']} {tie['a']['name']} vs #{tie['seed_b']} {tie['b']['name']} -> "
                  f"{tie[tie['winner']]['name']} {m['games']}")
    tm = anon.get("/api/matches?kind=tournament&limit=100").json()["items"]
    assert len(tm) == 7, len(tm)
    lst = anon.get("/api/tournaments").json()
    assert any(x["id"] == tid and x["status"] == "done" for x in lst)
    # ratings: tournament matches must not have moved anyone (ladder games may still run, so compare per match history)
    for x in anon.get("/api/ladder?limit=100").json()["items"]:
        mid = x["model"]["id"]
        hist = anon.get(f"/api/models/{mid}").json()["rating_history"]
        assert not any(h["match_id"] in {m["id"] for m in tm} for h in hist), mid
    print(f"PASS tournament {tid}: 7 matches, bracket complete, champion "
          f"{t['rounds'][2][0][t['rounds'][2][0]['winner']]['name']}, no rating changes ({len(before)} models on the ladder)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
