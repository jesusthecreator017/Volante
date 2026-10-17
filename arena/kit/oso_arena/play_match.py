"""Play one full 15-point game (policy vs policy) and write it as a replay.bin the viewer plays.
  python arena/kit/oso_arena/play_match.py [--ckpt out/badsim/best.pt] [--seed 0] [--std-scale 0.3] [--out out/badsim/match.bin]
Then: `oso-imu --no-imu` adds the rule-labelled `swings` track (a BFMD classifier relabels it when ~/Datasets/BFMD-work/stroke_hgb.joblib
exists, unless --no-classify) and out/badsim/match.json holds the score sheet.

Layout: lead-in 1 s, then every rally (60 fps frames taken from the 120 Hz sim log), then a 4 s gap between rallies in which the players
walk to the next service positions (the shuttle is hidden), so rallies are always more than the stats' 3 s rally gap apart.
Bodies: tools/synth_motion.py player_pose along the sim's root paths, swings timed so the forward-swing peak is the contact
(pitch tilted to the contact height), facing the shuttle around a contact and the net otherwise, lunge = hip drop, jump = hip rise.
Tracks: joints3d [F,2,17,4], shuttle [F,8] (flight_id per shot, conf 1 in flight), events (1 contact, 2 landing, 3 point)."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
# ponytail: path shim to the Replay tools until phase 14.2 vendors the needed parts into oso_arena.animate
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the kit root
import replay  # noqa: E402
import synth_motion as sm  # noqa: E402
from oso_arena import sim  # noqa: E402
FPS = 60.0
LEAD_S, TAIL_S = 1.0, 1.5
DECEL_S, WALK_S, REST_S = 0.4, 2.2, 1.4  # between rallies (sum 4.0 s)
SHOULDER_Y, REACH_FROM_SHOULDER = 1.4, 1.1  # standing shoulder height; shoulder -> racquet head centre at full stretch
CLASSIFIER = Path("~/Datasets/BFMD-work/stroke_hgb.joblib").expanduser()


def run_game(act, seed, max_steps=300_000):
    """Plays until the first game over with act(obs [2, O]) -> actions [2, 6]; returns (serialize_game(0), game-over step)."""
    s = sim.BadSim(1, seed, log_game=0)
    s.shaping = 0.0
    obs = s._obs()
    for _ in range(max_steps):
        obs, _, _, _ = s.step(act(obs[0])[None])
        if any(e["kind"] == "game_over" for e in s.log["events"]):
            break
    else:
        raise RuntimeError("game did not finish")
    g = s.serialize_game(0)
    return g, next(e["step"] for e in g["events"] if e["kind"] == "game_over")


def play_game(ckpt, seed, std_scale):
    """Policy vs policy (stochastic, std scaled down)."""
    import torch

    from oso_arena.train import load_net

    net = load_net(ckpt)
    torch.manual_seed(seed)

    def act(obs):
        with torch.no_grad():
            return net.act(torch.as_tensor(obs, dtype=torch.float32), False, std_scale)[0].numpy()

    return run_game(act, seed)


def split_rallies(g, stop):
    """[dict(s0, land, hits, point)] from the event log; rallies without a point (lets) are skipped."""
    out, cur = [], None
    for e in g["events"]:
        if e["step"] > stop + 1:
            break
        if e["kind"] == "start":
            cur = dict(s0=e["step"], hits=[], land=None, point=None, server=e["s"], score=e["score"])
        elif cur is None:
            continue
        elif e["kind"] == "hit":
            cur["hits"].append(e)
        elif e["kind"] == "land":
            cur["land"] = e
        elif e["kind"] == "point":
            cur["point"] = e
            out.append(cur)
    return out


def min_jerk(u):
    return sm.mj(u)


def build(g, stop):
    """Concatenate lead-in, rallies and gaps into per-frame arrays + events. Returns a dict."""
    rallies = split_rallies(g, stop)
    pos, shuttle, events, ev_hits = [], [], [], [[], []]
    flight = 0
    sheet = []
    first = rallies[0]
    start_pos = g["pos"][first["s0"]]
    pos += [start_pos] * int(LEAD_S * FPS)
    shuttle += [np.zeros(8)] * int(LEAD_S * FPS)
    for ri, r in enumerate(rallies):
        s0, L = r["s0"], r["land"]["step"]
        F0 = len(pos)
        steps = np.arange(s0, L, 2)
        hit_steps = np.array([h["step"] for h in r["hits"]], int)
        # shuttle rows: a hit makes its own step's row the first of a new flight, with the post-hit velocity
        fid = flight + np.array([(hit_steps <= st).sum() for st in steps])
        sh = np.zeros((len(steps), 8))
        sh[:, :6] = g["shuttle"][steps]
        for h in r["hits"]:
            j = np.nonzero(steps == h["step"])[0]
            if len(j):
                sh[j[0], 3:6] = h["v"]
        sh[:, 6], sh[:, 7] = 1.0, fid
        p = g["pos"][steps]
        t_of = lambda step, F0=F0, s0=s0: (F0 + (step - s0) / 2) / FPS  # noqa: E731
        lx, lz = r["land"]["x"], r["land"]["z"]
        land_t = t_of(L)
        if r["point"]["reason"] == "net" and sh[-1, 1] > 0.12:  # into the net: the shuttle drops from where it crossed the tape
            a, b = g["shuttle"][L - 1, :3], g["shuttle"][L, :3]
            q = a + (b - a) * (a[0] / (a[0] - b[0]))
            v, extra = np.zeros(3), []
            nid = int(fid[-1]) + 1
            while q[1] > 0:
                extra.append(np.concatenate([np.maximum(q, [-9, 0, -9]), v, [1.0, nid]]))
                q, v = sim.rk2(q, v)
            sh = np.vstack([sh, extra])
            p = np.vstack([p, np.repeat(p[-1:], len(extra), 0)])
            lx, lz = float(extra[-1][0]), float(extra[-1][2])
            land_t = (F0 + len(sh) - 1) / FPS
            flight_after = nid
        else:
            flight_after = int(fid[-1])
        shuttle += list(sh)
        pos += list(p)
        for h in r["hits"]:
            t = t_of(h["step"])
            events.append([t, 1, h["p"], *h["pos"]])
            ev_hits[h["p"]].append(dict(t=t, h=h["h"], lunge=h["lunge"], jump=h["jump"], at=np.array(h["pos"])))
        events.append([land_t, 2, -1, lx, 0.0, lz])
        w = r["point"]["winner"]
        sc = r["point"]["score"]
        events.append([land_t, 3, w, sc[w], sc[1 - w], 0.0])
        sheet.append(dict(rally=ri + 1, start_t=t_of(s0), end_t=land_t, server=int(r["server"]), shots=len(r["hits"]),
                          winner=int(w), reason=r["point"]["reason"], score=[int(sc[0]), int(sc[1])]))
        flight = flight_after + 1
        # gap to the next rally (or the tail)
        if ri + 1 < len(rallies):
            nxt = g["pos"][rallies[ri + 1]["s0"]]
            gap = gap_frames(np.array(pos[-1]), np.array(pos[-1]) - np.array(pos[-2]), nxt)
        else:
            gap = [np.array(pos[-1])] * int(TAIL_S * FPS)
        pos += gap
        shuttle += [np.zeros(8)] * len(gap)
    return dict(pos=np.array(pos), shuttle=np.array(shuttle), events=np.array(sorted(events, key=lambda e: (e[0], e[1]))),
                hits=ev_hits, sheet=sheet)


def gap_frames(p_end, dp, p_next):
    """Players [2,2] decelerate (distance dp/frame * decel), walk with a minimum-jerk blend to p_next, then rest."""
    out = []
    nd, nw, nr = int(DECEL_S * FPS), int(WALK_S * FPS), int(REST_S * FPS)
    v = dp * FPS
    mid = p_end + v * DECEL_S / 2
    mid[:, 0] = np.sign(p_end[:, 0]) * np.clip(np.abs(mid[:, 0]), 0.5, 7.4)  # stays on its own side
    mid[:, 1] = np.clip(mid[:, 1], -3.5, 3.5)
    coast = mid - p_end
    for i in range(1, nd + 1):
        u = i / nd
        out.append(p_end + coast * (1 - (1 - u) ** 2))
    for i in range(1, nw + 1):
        out.append(mid + (p_next - mid) * min_jerk(i / nw))
    out += [p_next] * nr
    return out


def swings_for(hits, rng):
    """[(start, forward arc deg, phases, pitch offset deg)] with the forward-swing peak at each contact."""
    f_len = sm.SWING[1]
    out = []
    ts = [h["t"] for h in hits]
    for i, h in enumerate(hits):
        gaps = [ts[i] - ts[i - 1]] if i else []
        gaps += [ts[i + 1] - ts[i]] if i + 1 < len(ts) else []
        s = float(np.clip(min(gaps, default=9) - f_len, 0.35, 1.0))
        b, ft, rt = sm.SWING[0] * s, sm.SWING[2] * s, sm.SWING[3] * s
        delta = float(rng.uniform(92, 112))
        sin_c = np.clip((h["h"] - SHOULDER_Y + (0.3 if h["lunge"] else 0.0)) / REACH_FROM_SHOULDER, -0.95, 1.0)
        theta_c = float(np.clip(np.degrees(np.arcsin(sin_c)), -65.0, 95.0))
        out.append((h["t"] - b - f_len / 2, delta, (b, f_len, ft, rt), theta_c - (150 - delta / 2)))
    return out


def facing_for(root, sign, t, hits, fps):
    """Unit horizontal facing [F, 3]: the net direction, turning to the shuttle from 0.6 s before to 0.15 s after each contact."""
    F = len(t)
    net = np.array([-sign, 0.0, 0.0])
    want = np.tile(net, (F, 1))
    for h in hits:
        lo, hi = np.searchsorted(t, [h["t"] - 0.6, h["t"] + 0.15])
        for i in range(lo, min(hi, F)):
            d = np.array([h["at"][0] - root[i, 0], 0.0, h["at"][2] - root[i, 1]])
            if np.linalg.norm(d) > 0.15:
                want[i] = d / np.linalg.norm(d)
    alpha = 1 - np.exp(-1 / fps / 0.12)
    out = np.empty_like(want)
    cur = net.copy()
    for i in range(F):
        cur = cur + alpha * (want[i] - cur)
        cur = cur / np.linalg.norm(cur)
        # no more than 85 degrees off the net direction
        if cur @ net < np.cos(np.radians(85)):
            cur = np.array([net[0] * np.cos(np.radians(85)), 0.0, np.sign(cur[2] or 1.0) * np.sin(np.radians(85))])
        out[i] = cur
    return out


def bodies(tl, seed):
    rng = np.random.default_rng(seed)
    F = len(tl["pos"])
    t = np.arange(F) / FPS
    joints = np.zeros((F, 2, 17, 4))
    for p, sign in enumerate((-1, 1)):
        hits = tl["hits"][p]
        root = tl["pos"][:, p]
        lunges = [h["t"] - 0.3 for h in hits if h["lunge"]]
        jumps = [h["t"] - sm.JUMP_S / 2 for h in hits if h["jump"]]
        j = sm.player_pose(root, sign, t, FPS, swings_for(hits, rng), lunges, jumps, facing_for(root, sign, t, hits, FPS))
        joints[:, p, :, :3] = j
        joints[:, p, :, 3] = 1.0
    return joints


def write_match(g, stop, out, seed):
    tl = build(g, stop)
    joints = bodies(tl, seed)
    replay.write(out, FPS, [
        ({"name": "joints3d", "layout": "x,y,z,conf", "keypoint_format": "coco17", "players": ["p0", "p1"]}, joints),
        ({"name": "shuttle", "layout": "x,y,z,vx,vy,vz,conf,flight_id"}, tl["shuttle"]),
        ({"name": "events", "layout": "t,type,player,x,y,z", "per_frame": False}, tl["events"]),
    ])
    return tl


def run(cmd):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"{' '.join(map(str, cmd))}\n{p.stdout}{p.stderr}")
    return p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", type=Path, default=ROOT / "out/badsim/best.pt")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--std-scale", type=float, default=0.3, help="policy noise relative to its trained std")
    ap.add_argument("--out", type=Path, default=ROOT / "out/badsim/match.bin")
    ap.add_argument("--no-classify", action="store_true")
    a = ap.parse_args()
    g, stop = play_game(a.ckpt, a.seed, a.std_scale)
    raw = a.out.with_suffix(".raw.bin")
    tl = write_match(g, stop, raw, a.seed)
    print(run(["cargo", "run", "--release", "--quiet", "--bin", "oso-imu", "--", "--no-imu", "--replay", raw, "-o", a.out]).strip().splitlines()[-1])
    raw.unlink()
    if CLASSIFIER.exists() and not a.no_classify:
        run([sys.executable, ROOT / "tools/classify_strokes.py", "--replay", a.out, "--model", CLASSIFIER, "-o", a.out])
        print("classified with", CLASSIFIER.name)
    final = tl["sheet"][-1]["score"]
    meta = dict(ckpt=str(a.ckpt), seed=a.seed, std_scale=a.std_scale, final_score=final, duration_s=len(tl["pos"]) / FPS,
                rallies=tl["sheet"], shots=int(sum(r["shots"] for r in tl["sheet"])))
    a.out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    print(f"final score {final[0]}-{final[1]}, {len(tl['sheet'])} rallies, {meta['shots']} shots, {meta['duration_s'] / 60:.1f} min -> {a.out}")


if __name__ == "__main__":
    main()
