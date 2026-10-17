"""Full-game evaluation: policy vs scripted bot and policy vs policy (200 games each by default).
  python arena/kit/oso_arena/evaluate.py [--ckpt out/badsim/best.pt | FILE.safetensors] [--games 200] [--out out/badsim/eval.json]
Reports win rate, rally length (shots), points per game, faults by type, shot mix and mean launch speed; prints a table and writes JSON."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from oso_arena import bot, sim  # noqa: E402
from oso_arena import builds as B  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
KINDS = ["unknown", "serve", "clear", "smash", "drop", "drive", "lift", "net"]

# Phase 6 rule thresholds, copied from src/imu/strokes.rs (classify). Keep both in sync. Heights/distances m, speeds m/s, angles deg.
SERVE_MAX_H, SMASH_MIN_H, SMASH_MAX_ANGLE, SMASH_MIN_V0 = 1.15, 2.0, -5.0, 25.0
NET_MAX_X_CONTACT, NET_MAX_APEX, NET_MAX_X_END = 2.2, 2.3, 2.5
DROP_MIN_H, DROP_MAX_X_END, DROP_MAX_V0 = 1.8, 3.2, 20.0
CLEAR_MIN_H, CLEAR_MIN_APEX, CLEAR_MIN_X_END = 1.8, 4.5, 4.5
LIFT_MAX_H, LIFT_MIN_APEX = 1.2, 3.5
DRIVE_MIN_H, DRIVE_MAX_H, DRIVE_APEX_ABOVE_H, DRIVE_MIN_V0 = 0.6, 1.9, 0.6, 12.0


def classify(first, h, x_contact, v0, angle, apex, x_end):
    """First match wins, same order as strokes.rs `classify`."""
    if first and h < SERVE_MAX_H:
        return "serve"
    if h > SMASH_MIN_H and angle < SMASH_MAX_ANGLE and v0 > SMASH_MIN_V0:
        return "smash"
    if x_contact < NET_MAX_X_CONTACT and apex < NET_MAX_APEX and x_end < NET_MAX_X_END:
        return "net"
    if h > DROP_MIN_H and x_end < DROP_MAX_X_END and v0 < DROP_MAX_V0:
        return "drop"
    if h > CLEAR_MIN_H and apex > CLEAR_MIN_APEX and x_end > CLEAR_MIN_X_END:
        return "clear"
    if h < LIFT_MAX_H and apex > LIFT_MIN_APEX:
        return "lift"
    if DRIVE_MIN_H < h < DRIVE_MAX_H and apex < h + DRIVE_APEX_ABOVE_H and v0 > DRIVE_MIN_V0:
        return "drive"
    return "unknown"


def shot_mix(rows):
    """rows of sim.shot_log -> (Counter of kind, mean launch speed)."""
    c = Counter(classify(r[2], r[3], r[4], r[5], r[6], r[7], r[8]) for r in rows)
    return c, (float(np.mean([r[5] for r in rows])) if rows else float("nan"))


def play_games(fa, fb, n, seed=0, max_steps=60000, sim_kw=None, builds=None):
    """A = fa, B = fb (each obs [m, O] -> actions [m, 6], applied per slot). A is player 0 in the first half of the games and
    player 1 in the second. builds = (build of A, build of B) dicts, default all-6. Runs until every game finished once (or
    max_steps). Returns a result dict."""
    a_slot = np.zeros(n, int)
    a_slot[n // 2:] = 1  # slot of A in each game
    bl = None
    if builds is not None:
        ba, bb = (B.to_array(b) for b in builds)
        bl = np.where((a_slot == 0)[:, None, None], np.stack([ba, bb])[None], np.stack([bb, ba])[None])
    s = sim.BadSim(n, seed, builds=bl)
    for k, v in (sim_kw or {}).items():
        setattr(s, k, v)
    s.shaping = 0.0
    s.log_shots = True
    is_a = np.zeros((n, 2), bool)
    is_a[np.arange(n), a_slot] = True
    obs = s._obs()
    finished = np.zeros(n, bool)
    fin_step = np.full(n, 10**9)
    final = np.zeros((n, 2), int)
    rallies = []  # (game, shots, reason, A won (None for a let))
    for step in range(max_steps):
        act = np.zeros((n, 2, sim.ACT_DIM))
        for p in (0, 1):
            aa, bb = fa(obs[:, p]), fb(obs[:, p])
            act[:, p] = np.where(is_a[:, p, None], aa, bb)
        obs, _, done, info = s.step(act)
        for g in np.nonzero(info["rally_end"] & ~finished)[0]:
            w = info["winner"][g]
            rallies.append((int(g), int(info["shots"][g]), int(info["reason"][g]), None if w < 0 else bool(is_a[g, w])))
        newly = done & ~finished
        final[newly] = info["final_score"][newly]
        fin_step[newly] = step + 1
        finished |= done
        if finished.all():
            break
    rows = [r for r in s.shot_log if r[9] <= fin_step[r[0]]]
    # A's score first
    a_pts = np.where(a_slot == 0, final[:, 0], final[:, 1])
    b_pts = np.where(a_slot == 0, final[:, 1], final[:, 0])
    return dict(n=n, finished=finished, a_pts=a_pts, b_pts=b_pts, rallies=rallies, shots=rows, a_slot=a_slot, steps=step + 1)


def summarize(r):
    f = r["finished"]
    nf = int(f.sum())
    rl = np.array([x[1] for x in r["rallies"] if x[2] != sim.LET]) if r["rallies"] else np.zeros(1)
    faults = Counter(sim.REASONS[x[2]] for x in r["rallies"])
    nr = max(len(r["rallies"]), 1)
    a_rows = [s for s in r["shots"] if _is_a(r, s)]
    mix_all, v_all = shot_mix(r["shots"])
    mix_a, v_a = shot_mix(a_rows)
    tot = max(sum(mix_all.values()), 1)
    ns = [r for r in r["shots"] if not r[2]]  # non-serve shots
    disp = np.array([r[10] for r in ns]) if ns else np.zeros(1)
    run = np.array([r[11] for r in ns]) if ns else np.zeros(1)
    return dict(
        disp_mean=float(disp.mean()), disp_median=float(np.median(disp)), opp_run_per_shot=float(run.mean()),
        games=r["n"], finished=nf,
        win_rate_A=float((r["a_pts"] > r["b_pts"])[f].mean()) if nf else None,
        points_per_game=float((r["a_pts"] + r["b_pts"])[f].mean()) if nf else None,
        mean_score_A=float(r["a_pts"][f].mean()) if nf else None, mean_score_B=float(r["b_pts"][f].mean()) if nf else None,
        rally_mean=float(rl.mean()), rally_max=int(rl.max()), rallies=len(r["rallies"]),
        faults_per_rally={k: faults[k] / nr for k in sim.REASONS},
        shot_mix={k: mix_all[k] / tot for k in KINDS}, shot_mix_A={k: mix_a[k] / max(sum(mix_a.values()), 1) for k in KINDS},
        launch_speed_mean=v_all, launch_speed_mean_A=v_a, shots=tot,
    )


def _is_a(r, s):
    return (s[1] == r["a_slot"][s[0]])


def make_policy(ckpt, deterministic=True):
    """A .safetensors submission (NumpyPolicy) or a .pt checkpoint of ours (trusted, torch) -> function obs [m, O] -> actions [m, 6]."""
    if str(ckpt).endswith(".safetensors"):
        from oso_arena.policy import NumpyPolicy

        return NumpyPolicy.load(ckpt).act
    import torch

    from oso_arena.train import load_net

    net = load_net(ckpt)
    nin = net.actor[0].in_features

    def act(obs):
        with torch.no_grad():
            return net.act(torch.as_tensor(obs[:, :nin], dtype=torch.float32), deterministic)[0].numpy()

    return act


def table(name, d):
    print(f"\n== {name} ==")
    print(f"games {d['finished']}/{d['games']} finished, win rate of A {d['win_rate_A']}, points/game {d['points_per_game']}, "
          f"mean score A {d['mean_score_A']} B {d['mean_score_B']}")
    print(f"rally shots: mean {d['rally_mean']:.2f}  max {d['rally_max']}  ({d['rallies']} rallies, {d['shots']} shots)")
    print("faults per rally: " + "  ".join(f"{k} {v:.3f}" for k, v in d["faults_per_rally"].items()))
    print("shot mix:         " + "  ".join(f"{k} {100 * v:.1f}%" for k, v in d["shot_mix"].items()))
    print(f"displacement d (non-serve): mean {d['disp_mean']:.2f} m  median {d['disp_median']:.2f} m; opponent run per shot {d['opp_run_per_shot']:.2f} m; "
          f"serve faults/rally {d['faults_per_rally']['serve']:.3f}")
    print(f"mean launch speed {d['launch_speed_mean']:.1f} m/s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", type=Path, default=ROOT / "out/badsim/best.pt")
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--max-steps", type=int, default=60000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=ROOT / "out/badsim/eval.json")
    ap.add_argument("--legacy", action="store_true", help="old .pt checkpoint (trusted, ours): 28-dim obs, no reaction delay (baseline measurement)")
    args = ap.parse_args()
    if args.legacy:
        sim.REACTION_S = 0.0
    pol = make_policy(args.ckpt)
    out = {}
    for name, opp in (("policy vs bot", lambda o: bot.bot_actions(o)), ("policy vs policy (self-play)", pol)):
        r = play_games(pol, opp, args.games, args.seed, args.max_steps)
        out[name] = summarize(r)
        table(name, out[name])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
