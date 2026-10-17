"""PPO self-play for the badminton simulator (small own PPO, PyTorch). Run it as
  oso-arena train --build 9,6,5,4 --reward my_reward.py --init base --minutes 60 --out runs/try1      (python -m oso_arena train ... works too)

What a run does. Games: 60 % self-play (the current policy on both slots), 30 % against a frozen snapshot from a pool (new snapshot every
SNAPSHOT_EVERY samples), 10 % against the scripted bot; the opponent assignment is redrawn every iteration. In every game the learner
plays one slot with its own build (--build); the other slot gets the opponent build (--opp-builds: random = a uniformly random valid
build per game, re-drawn whenever a game restarts, or one fixed build). Reward: +1 / -1 per rally and the serve-fault penalty (from the
simulator), plus the shaping from oso_arena.rewards: the default reward, or your own --reward FILE.py. The default shaping is the
Phase 13.2 one (net-crossing term annealed to 0 over the first 30 % of the time budget, depth, displacement, base position); curriculum:
reach x1.3 ramping to the real value by 40 %. lr 1e-4 -> 3e-5.

Files in --out (default runs/<timestamp>): latest.safetensors + latest.opt.pt (resume), best.safetensors, ckpt_NNNmin.safetensors,
final.safetensors, train_log.jsonl, ckpt_eval.jsonl, run.json. A .safetensors checkpoint is the full training state (actor, critic,
log_std; the header carries the build, elapsed time and schedule); `oso-arena export runs/x/best -o m.safetensors --name N` turns it
into the submission file and takes the build from that header. The optimizer / snapshot file is a torch file written by this program
and loaded only with weights_only=True. Nothing here uses pickle on files from others.

best = highest self-play displacement among checkpoints with bot win rate >= 80 %, rally >= 6, serve faults <= 0.05 (else the highest
with win rate >= 80 %, else the highest displacement); --early-stop ends the run when those gates pass at 2 consecutive checkpoints
(for the all-6 build also displacement >= 1.5x DISP_BASELINE). The PPO update runs on MPS when available."""
import argparse
import copy
import datetime
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

try:
    import torch
    import torch.nn as nn
except ImportError:  # the kit's other commands never import this module
    raise ImportError("training needs PyTorch: install the kit with the train extra (pip install 'oso_arena-*.whl[train]') or pip install torch") from None

from oso_arena import trainargs
from oso_arena import CONTRACT, KIT_VERSION, basemodel, bot, evaluate, export, rewards, sim
from oso_arena import builds as B

N_GAMES, HORIZON = trainargs.N_GAMES, trainargs.HORIZON  # Phase 13.2: 256 -> 1024 (profiled: the PPO update dominated; it runs on MPS)
GAMMA, LAMBDA, CLIP, VF_COEF, MAX_GRAD = 0.997, 0.95, 0.2, 0.5, 0.5
EPOCHS, MINIBATCH, TARGET_KL = 4, 16384, 0.03  # same update count per iteration as 256 games / 4096
LR0, LR1 = float(os.environ.get("BADSIM_LR0", 1e-4)), float(os.environ.get("BADSIM_LR1", 3e-5))
ENT0, ENT1 = 6e-3, 2e-4  # tuning round: ENT0 3e-3 -> 6e-3
P_SELF, P_SNAP = 0.6, 0.3  # remaining 0.1: bot
SNAPSHOT_EVERY = 5_000_000  # samples (player-slot steps)
POOL_MAX = 12
LOG_EVERY_S, CKPT_EVERY_S = 120, trainargs.CKPT_EVERY_S
HIDDEN = 256
STATE_FORMAT = "oso-arena-train/1"
GATE_WR, GATE_RALLY, GATE_SERVE_FAULT, GATE_DISP_X = 0.80, 6.0, 0.05, 1.5
DISP_BASELINE = 1.62  # Phase 13.1 best.pt, self-play mean displacement of non-serve shots (all-6 build, 200 games)


def _finite(o):
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return None if isinstance(o, float) and not np.isfinite(o) else o


def jdump(o):
    """Strict JSON: NaN / infinity (e.g. a launch speed with no shots) become null."""
    return json.dumps(_finite(o), allow_nan=False)


def pick_device(name="auto"):
    if name == "auto":
        name = "cpu" if os.environ.get("BADSIM_CPU") or os.environ.get("OSO_ARENA_CPU") else "mps" if torch.backends.mps.is_available() else "cpu"
    return torch.device(name)


def mlp(i, o, last_gain):
    layers = [nn.Linear(i, HIDDEN), nn.Tanh(), nn.Linear(HIDDEN, HIDDEN), nn.Tanh(), nn.Linear(HIDDEN, o)]
    for m, g in zip((layers[0], layers[2], layers[4]), (2**0.5, 2**0.5, last_gain)):
        nn.init.orthogonal_(m.weight, g)
        nn.init.zeros_(m.bias)
    return nn.Sequential(*layers)


class ActorCritic(nn.Module):
    def __init__(self, obs_dim=None):
        super().__init__()
        obs_dim = obs_dim or sim.OBS_DIM
        self.actor = mlp(obs_dim, sim.ACT_DIM, 0.01)
        self.critic = mlp(obs_dim, 1, 1.0)
        # tuning round: wider initial exploration on the aim x, aim z and flight-time dims (3, 4, 5) so deep shots get tried
        self.log_std = nn.Parameter(torch.tensor([-0.5, -0.5, -0.5, 0.0, 0.0, 0.0]))

    def dist(self, obs):
        return torch.distributions.Normal(self.actor(obs), self.log_std.clamp(-3.0, 0.5).exp())

    def act(self, obs, deterministic=False, std_scale=1.0):
        """(raw action, log prob). The env clips the action to [-1, 1]; the raw sample is what the log prob refers to."""
        mean = self.actor(obs)
        if deterministic:
            return mean, None
        std = self.log_std.clamp(-3.0, 0.5).exp() * std_scale
        d = torch.distributions.Normal(mean, std)
        a = d.sample()
        return a, d.log_prob(a).sum(-1)

    def value(self, obs):
        return self.critic(obs).squeeze(-1)


# ---------------------------------------------------------------- checkpoints (safetensors; no pickle)
def save_state(path, net, **meta):
    """The full training state (actor, critic, log_std) as safetensors; `meta` (str-able values) goes in the header."""
    from safetensors.torch import save_file

    tensors = {k: v.detach().cpu().contiguous().clone() for k, v in net.state_dict().items()}
    head = {"format": STATE_FORMAT, "contract": CONTRACT, "kit_version": KIT_VERSION}
    head.update({k: v if isinstance(v, str) else json.dumps(_finite(v), separators=(",", ":"), allow_nan=False) for k, v in meta.items()})
    save_file(tensors, str(path), metadata=head)


def read_state(path):
    """(tensors {name: torch tensor}, header dict) of a .safetensors training state or submission file."""
    from safetensors import safe_open

    with safe_open(str(path), framework="pt", device="cpu") as f:
        return {k: f.get_tensor(k) for k in f.keys()}, dict(f.metadata() or {})


def load_net(path):
    """A training state (.safetensors, or a .pt checkpoint of ours: loaded with weights_only=True) -> ActorCritic in eval mode."""
    path = export.state_path(path)
    if str(path).endswith(".safetensors"):
        sd, _ = read_state(path)
    else:
        sd = torch.load(path, map_location="cpu", weights_only=True)["model"]
    net = ActorCritic(sd["actor.0.weight"].shape[1])
    net.load_state_dict(sd)
    return net.eval()


def np_policy(net, deterministic=True, std_scale=1.0):
    def act(obs):
        with torch.no_grad():
            return net.act(torch.as_tensor(obs, dtype=torch.float32), deterministic, std_scale)[0].numpy()

    return act


def init_net(net, source):
    """Weights for a fresh run: `base` (the packaged base model) or a .safetensors file (a full training state, or a submission file whose
    actor is used with a fresh critic). Returns a note to print."""
    path = basemodel.path(full=True) if source == "base" else Path(source)
    if not path.is_file():
        raise ValueError(f"--init file not found: {path}")
    if not str(path).endswith(".safetensors"):
        raise ValueError("--init takes 'base' or a .safetensors file (a training state or a submission file); .pt files are not loaded")
    sd, head = read_state(path)
    if "actor.0.weight" not in sd:
        raise ValueError(f"{path} has no actor.0.weight: not an oso-arena model")
    n_in = sd["actor.0.weight"].shape[1]
    if n_in != sim.OBS_DIM:
        raise ValueError(f"{path} expects {n_in} observation inputs but contract {CONTRACT} has {sim.OBS_DIM}")
    full = all(k in sd for k in net.state_dict())
    if full:
        net.load_state_dict(sd)
        return f"initialised from {path.name} (actor, critic, log_std)"
    actor = {k: v.float() for k, v in sd.items() if k.startswith("actor.")}
    net.load_state_dict(actor, strict=False)
    return f"initialised the actor from {path.name} (a submission file: fresh critic and exploration noise)"


# ---------------------------------------------------------------- PPO helpers
def gae(rew, val, last_val, done):
    """rew, val [T, n, 2], done [T, n] (the game ended after step t), last_val [n, 2]."""
    T = len(rew)
    adv = np.zeros_like(rew)
    last = 0.0
    nd = (1.0 - done)[..., None]
    for t in reversed(range(T)):
        nxt = last_val if t == T - 1 else val[t + 1]
        delta = rew[t] + GAMMA * nxt * nd[t] - val[t]
        last = delta + GAMMA * LAMBDA * nd[t] * last
        adv[t] = last
    return adv, adv + val


def _pair_builds(n, slot, a, b):
    """Builds [n, 2, 4] with `a` ([4] or [n, 4]) in each game's `slot` and `b` in the other slot."""
    a, b = np.broadcast_to(a, (n, 4)), np.broadcast_to(b, (n, 4))
    out = np.empty((n, 2, 4), int)
    for p in (0, 1):
        out[:, p] = np.where((slot == p)[:, None], a, b)
    return out


def quick_stats(net, build, n=64, steps=1500, seed=0):
    """Short deterministic self-play and policy-vs-bot runs (the policy has `build`, the bot the all-6 build): rally length, faults, shot mix,
    point win rate vs the bot."""
    pol = np_policy(net)
    out = {}
    L, A6 = B.to_array(build), B.to_array(B.ALL6)
    for name, opp in (("self", pol), ("bot", bot.bot_actions)):
        slot = np.arange(n) % 2  # the policy's slot (self-play: both)
        s = sim.BadSim(n, seed, builds=_pair_builds(n, slot, L, L if name == "self" else A6))
        s.log_shots = True
        obs = s._obs()
        wins = losses = 0
        lens = []
        faults = Counter()
        for _ in range(steps):
            act = np.zeros((n, 2, sim.ACT_DIM))
            for p in (0, 1):
                a, b = pol(obs[:, p]), opp(obs[:, p])
                act[:, p] = a if name == "self" else np.where((slot == p)[:, None], a, b)
            obs, _, _, info = s.step(act)
            for g in np.nonzero(info["rally_end"])[0]:
                faults[sim.REASONS[info["reason"][g]]] += 1
                if info["reason"][g] != sim.LET:
                    lens.append(int(info["shots"][g]))
                if name == "bot" and info["winner"][g] >= 0:
                    wins += info["winner"][g] == slot[g]
                    losses += info["winner"][g] != slot[g]
        mix, v = evaluate.shot_mix(s.shot_log)
        tot = max(sum(mix.values()), 1)
        nr = max(sum(faults.values()), 1)
        out[name] = dict(rally_mean=float(np.mean(lens)) if lens else 0.0, rallies=nr,
                         faults={k: round(faults[k] / nr, 3) for k in sim.REASONS},
                         mix={k: round(mix[k] / tot, 3) for k in evaluate.KINDS}, v0=round(v, 1))
        if name == "bot":
            out[name]["point_win_rate"] = wins / max(wins + losses, 1)
    return out


def full_eval_vs_bot(net, build, n=64, max_steps=20000):
    r = evaluate.play_games(np_policy(net), bot.bot_actions, n, seed=123, max_steps=max_steps, builds=(build, B.ALL6))
    d = evaluate.summarize(r)
    f = r["finished"]
    wr = float((r["a_pts"] > r["b_pts"])[f].mean()) if f.any() else 0.0
    share = float(r["a_pts"].sum() / max((r["a_pts"] + r["b_pts"]).sum(), 1))
    return wr, share, d


def full_eval_self(net, build, n=64, max_steps=20000):
    """Deterministic self-play full games: the evaluate.summarize dict."""
    pol = np_policy(net)
    return evaluate.summarize(evaluate.play_games(pol, pol, n, seed=124, max_steps=max_steps, builds=(build, build)))


def redraw_opponents(game, idx, lslot, learner, opp, rng, obs):
    """Games `idx` restarted: draw them a new uniformly random opponent build (written to opp[idx]), give it to the opponent slot (the
    learner keeps its build) and patch the build columns of the observation `obs` that `step` already returned for the new game."""
    opp[idx] = B.random_builds(rng, len(idx))
    game.set_builds(idx, _pair_builds(len(idx), lslot[idx], learner, opp[idx]))
    obs[idx, :, sim.OBS_BASE:] = game._build_obs[idx]


def parse_opp_builds(spec):
    """'random' -> None, else a build dict."""
    return None if spec == "random" else B.parse(spec)


parse_args = trainargs.parse_args  # argparse lives in trainargs.py so `train --help` works without torch


def run_guard(fn, build, opp_build, args):
    """Estimates what the reward pays per rally and player (bot vs bot and random play) and warns above the limit."""
    out = {}
    for pol in ("bot", "random"):
        r = rewards.shaping_guard(fn, build, opp_build, policy=pol, n=args.guard_games, steps=args.guard_steps, seed=args.seed)
        out[pol] = r
        print(f"shaping guard ({pol}): {r['rallies']} rallies, shaping per rally and player: mean {r['mean']:.3f}, 95th pct {r['p95']:.3f}, max {r['max']:.3f} "
              f"(limit {rewards.SHAPING_LIMIT:g})", flush=True)
    worst = max(max(r["mean"], r["p95"]) for r in out.values())
    if worst > rewards.SHAPING_LIMIT:
        print(f"WARNING: your reward can pay {worst:.2f} points per rally and player, more than {rewards.SHAPING_LIMIT:g}. That is worth more than winning a rally: "
              "the model can learn to keep rallies going together (cooperation) instead of winning them. Cap how many shots pay "
              "(like the default's first 10) or scale the terms down.", flush=True)
    return out


def run(args):
    build = B.parse(args.build) if args.build else None
    out_dir = args.out or Path("runs") / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    latest = out_dir / "latest.safetensors"
    if latest.exists() and not args.resume:
        raise ValueError(f"{out_dir} already holds a run (latest.safetensors): add --resume to continue it, or choose a new --out")
    resuming = bool(args.resume and latest.exists())
    prev = {}
    if resuming and (out_dir / "run.json").exists():
        prev = json.loads((out_dir / "run.json").read_text())
    reward_path = args.reward or (Path(prev["reward"]) if prev.get("reward") else None)
    opp_spec = args.opp_builds or prev.get("opp_builds") or "random"
    for flag, given, old in (("--reward", str(args.reward) if args.reward else None, prev.get("reward")), ("--opp-builds", args.opp_builds, prev.get("opp_builds"))):
        if resuming and given and old and given != old:
            print(f"warning: {flag} {given} differs from the resumed run's {old}; using {given}", flush=True)
    opp_fixed = parse_opp_builds(opp_spec)
    reward_fn = rewards.load_reward_file(reward_path) if reward_path else None
    if args.resume and not resuming:
        print(f"nothing to resume in {out_dir}: starting a new run")
    ck = None
    if resuming:
        tensors, head = read_state(latest)
        run_build = json.loads(head["build"]) if "build" in head else None
        if build is not None and run_build is not None and build != run_build:
            raise ValueError(f"--build {args.build} differs from the build of the run being resumed {json.dumps(run_build, separators=(',', ':'))}")
        build = build or run_build
        ck = torch.load(out_dir / "latest.opt.pt", map_location="cpu", weights_only=True)
    build = build or dict(B.ALL6)
    if not resuming and args.init:  # fail before anything is written
        init_net(ActorCritic(), args.init)
    out_dir.mkdir(parents=True, exist_ok=True)
    bstr = json.dumps(build, separators=(",", ":"))
    (out_dir / "run.json").write_text(json.dumps(dict(
        build=build, opp_builds=opp_spec, reward=str(reward_path) if reward_path else None, init=args.init or prev.get("init"), seed=args.seed,
        minutes=args.minutes, kit_version=KIT_VERSION, games=args.games, horizon=args.horizon), indent=1))
    if args.guard_games > 0:
        run_guard(reward_fn, build, opp_fixed, args)
    dev = pick_device(args.device)
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    L = B.to_array(build)
    net = ActorCritic()
    gnet = ActorCritic().to(dev)  # the PPO update runs here (MPS); rollouts use the CPU copy `net`
    opt = torch.optim.Adam(gnet.parameters(), lr=LR0, eps=1e-5)
    pool, elapsed, samples, it, next_snap, best = [], 0.0, 0, 0, SNAPSHOT_EVERY, -1.0  # best = tier * 1000 + displacement of best.safetensors
    if resuming:
        net.load_state_dict(tensors)
        gnet.load_state_dict(tensors)
        opt.load_state_dict(ck["opt"])
        pool, samples, it, next_snap, best = ck["pool"], ck["samples"], ck["it"], ck["next_snap"], float(ck["best"])
        elapsed = float(head["elapsed"])
        print(f"resumed at {elapsed / 60:.1f} min, {samples / 1e6:.1f} M samples")
    elif args.init:
        print(init_net(net, args.init))
        gnet.load_state_dict(net.state_dict())
        elapsed = args.init_elapsed * 60
    budget = max(args.minutes * 60, 1e-6)
    n, O, A, T = args.games, sim.OBS_DIM, sim.ACT_DIM, args.horizon
    game = sim.BadSim(n, args.seed + 1000 * it)
    opp = np.broadcast_to(B.to_array(opp_fixed), (n, 4)).copy() if opp_fixed else B.random_builds(rng, n)  # each game's opponent build
    lslot = rng.integers(0, 2, n)  # the learner's slot in each game (redrawn every iteration)
    game.set_builds(slice(None), _pair_builds(n, lslot, L, opp))
    obs = game._obs()
    snap_nets = []
    streak = 0  # consecutive checkpoints that pass every gate
    is_all6 = build == B.ALL6

    def make_snap_nets():
        snap_nets.clear()
        for sd in pool:
            m = ActorCritic()
            m.load_state_dict(sd)
            snap_nets.append(m.eval())

    def save_all(path_stem, elapsed, sched, **extra):
        save_state(out_dir / f"{path_stem}.safetensors", net, build=bstr, elapsed=elapsed, samples=samples, it=it, schedule=sched, **extra)

    def save_latest(elapsed, sched):
        save_all("latest", elapsed, sched)
        torch.save(dict(opt=opt.state_dict(), pool=pool, samples=samples, it=it, next_snap=next_snap, best=best), out_dir / "latest.opt.pt")

    make_snap_nets()
    t_run0 = time.time() - elapsed
    t_log = t_ckpt = time.time()
    win = dict(samples=0, bot_w=0, bot_l=0, rallies=0, shots=0, faults=Counter(), t=time.time(), loss=[])
    curve = open(out_dir / "train_log.jsonl", "a")
    ckpt_s = args.ckpt_minutes * 60
    print(f"training build {bstr} vs {'random builds' if opp_fixed is None else 'build ' + json.dumps(opp_fixed, separators=(',', ':'))}, "
          f"{args.minutes:g} min, reward: {reward_path or 'default'}, out: {out_dir}", flush=True)

    while True:
        elapsed = time.time() - t_run0
        prog = min(elapsed / budget, 1.0)
        # --- schedules
        shaping = 0.1 * max(0.0, 1.0 - prog / 0.3)  # the default reward's net-crossing term (a custom reward may read the schedule it likes)
        base_scale = 1.0  # Phase 13.2 tuning round: no anneal (annealing it to 0 at 25 % collapsed the game into net camping)
        c = min(prog / 0.4, 1.0)
        game.noise_scale, game.reach_scale = 1.0, 1.3 - 0.3 * c  # tuning round: real aim noise from the start
        lr = LR0 + (LR1 - LR0) * prog
        for g_ in opt.param_groups:
            g_["lr"] = lr
        ent = ENT0 + (ENT1 - ENT0) * prog
        sched = dict(prog=round(prog, 4), lr=lr, entropy=ent, shaping=shaping, base_scale=base_scale, reach_scale=game.reach_scale, minutes=args.minutes)
        # --- opponents for this iteration
        r = rng.random(n)
        otype = np.where(r < P_SELF, 0, np.where(r < P_SELF + P_SNAP, 1, 2))
        if not snap_nets:
            otype[otype == 1] = 0
        lslot = rng.integers(0, 2, n)  # the learner's slot (self-play games: the learner's own slot; the other slot is an opponent build)
        snap_id = rng.integers(0, max(len(snap_nets), 1), n)
        game.set_builds(slice(None), _pair_builds(n, lslot, L, opp))
        obs[..., sim.OBS_BASE:] = game._build_obs
        learn = np.ones((n, 2), bool)
        for p in (0, 1):  # a slot learns when it plays the learner's build and is the learner's slot (or self-play against that same build)
            same = (game.builds[:, p] == L).all(-1)
            learn[:, p] = same & ((lslot == p) | (otype == 0))
        # --- rollout
        B_obs = np.zeros((T, n, 2, O), np.float32)
        B_act = np.zeros((T, n, 2, A), np.float32)
        B_lp = np.zeros((T, n, 2), np.float32)
        B_val = np.zeros((T, n, 2), np.float32)
        B_rew = np.zeros((T, n, 2), np.float32)
        B_done = np.zeros((T, n), np.float32)
        net.eval()
        for t in range(T):
            o = torch.as_tensor(obs.reshape(-1, O))
            with torch.no_grad():
                a, lp = net.act(o)
                v = net.value(o)
            B_obs[t], B_act[t] = obs, a.numpy().reshape(n, 2, A)
            B_lp[t], B_val[t] = lp.numpy().reshape(n, 2), v.numpy().reshape(n, 2)
            act = B_act[t].copy()
            sel = np.nonzero(otype == 2)[0]
            if len(sel):
                oslot = 1 - lslot[sel]
                act[sel, oslot] = bot.bot_actions(obs[sel, oslot])
            for k in set(snap_id[otype == 1].tolist()):
                sel = np.nonzero((otype == 1) & (snap_id == k))[0]
                oslot = 1 - lslot[sel]
                with torch.no_grad():
                    act[sel, oslot] = snap_nets[k].act(torch.as_tensor(obs[sel, oslot]))[0].numpy()
            obs, rew, done, info = game.step(act)
            B_rew[t] = rewards.total_reward(rew, game.events(), reward_fn, shaping=shaping, base_scale=base_scale)
            B_done[t] = done
            for g in np.nonzero(info["rally_end"])[0]:
                if info["reason"][g] == sim.LET:
                    win["faults"]["let"] += 1
                    continue
                win["rallies"] += 1
                if otype[g] == 0:
                    win["shots"] += int(info["shots"][g])
                    win["faults"][sim.REASONS[info["reason"][g]]] += 1
                elif otype[g] == 2:
                    win["bot_w"] += int(info["winner"][g] == lslot[g])
                    win["bot_l"] += int(info["winner"][g] != lslot[g])
            if opp_fixed is None and done.any():  # a game restarted: a new random opponent build for it
                redraw_opponents(game, np.nonzero(done)[0], lslot, L, opp, rng, obs)
        with torch.no_grad():
            last_val = net.value(torch.as_tensor(obs.reshape(-1, O))).numpy().reshape(n, 2)
        adv, ret = gae(B_rew, B_val, last_val, B_done)
        m = np.broadcast_to(learn[None], (T, n, 2))
        X = torch.as_tensor(B_obs[m]).to(dev)
        Y = dict(act=torch.as_tensor(B_act[m]), lp=torch.as_tensor(B_lp[m]), adv=torch.as_tensor(adv[m]), ret=torch.as_tensor(ret[m]))
        Y = {k_: v_.to(dev) for k_, v_ in Y.items()}
        Y["adv"] = (Y["adv"] - Y["adv"].mean()) / (Y["adv"].std() + 1e-8)
        k = len(X)
        samples += k
        win["samples"] += k
        # --- PPO update
        stop = False
        for _ in range(EPOCHS):
            perm = torch.randperm(k, device=dev)
            for i in range(0, k, MINIBATCH):
                ix = perm[i:i + MINIBATCH]
                d = gnet.dist(X[ix])
                lp = d.log_prob(Y["act"][ix]).sum(-1)
                ratio = (lp - Y["lp"][ix]).exp()
                pg = -torch.min(ratio * Y["adv"][ix], ratio.clamp(1 - CLIP, 1 + CLIP) * Y["adv"][ix]).mean()
                vl = (gnet.value(X[ix]) - Y["ret"][ix]).pow(2).mean()
                loss = pg + VF_COEF * vl - ent * d.entropy().sum(-1).mean()
                opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(gnet.parameters(), MAX_GRAD)
                opt.step()
                kl = (Y["lp"][ix] - lp).mean().item()
                if kl > TARGET_KL:
                    stop = True
                    break
            if stop:
                break
        net.load_state_dict({k_: v_.cpu() for k_, v_ in gnet.state_dict().items()})
        win["loss"].append((pg.item(), vl.item()))
        it += 1
        if samples >= next_snap:
            pool.append({k_: v_.clone() for k_, v_ in net.state_dict().items()})
            pool[:] = pool[-POOL_MAX:]
            make_snap_nets()
            next_snap += SNAPSHOT_EVERY
        now = time.time()
        done_training = elapsed >= budget
        if now - t_log >= LOG_EVERY_S or done_training:
            q = quick_stats(net.eval(), build, n=args.eval_games, steps=min(1500, args.eval_steps))
            sps = win["samples"] / max(now - win["t"], 1e-9)
            wl = win["bot_w"] + win["bot_l"]
            row = dict(min=round(elapsed / 60, 1), it=it, msamples=round(samples / 1e6, 3), sps=int(sps), prog=round(prog, 3),
                       shaping=round(shaping, 3), base=round(base_scale, 2), noise=round(game.noise_scale, 2), reach=round(game.reach_scale, 2),
                       train_selfplay_rally=round(win["shots"] / max(sum(win["faults"][k] for k in sim.REASONS[:-1]), 1), 2),
                       train_bot_point_wr=round(win["bot_w"] / max(wl, 1), 3), pg=round(float(np.mean([l[0] for l in win["loss"]])), 4),
                       vl=round(float(np.mean([l[1] for l in win["loss"]])), 4), std=round(float(net.log_std.detach().exp().mean()), 3),
                       eval=q, pool=len(pool))
            print(jdump(row), flush=True)
            curve.write(jdump(row) + "\n")
            curve.flush()
            win.update(samples=0, bot_w=0, bot_l=0, rallies=0, shots=0, faults=Counter(), t=now, loss=[])
            t_log = now
        if now - t_ckpt >= ckpt_s or done_training:
            save_latest(elapsed, sched)
            wr, share, d = full_eval_vs_bot(net.eval(), build, n=args.eval_games, max_steps=args.eval_steps)
            print(f"checkpoint @ {elapsed / 60:.1f} min: full games vs bot: win rate {wr:.3f}, point share {share:.3f}, "
                  f"rally mean {d['rally_mean']:.1f}", flush=True)
            ds = full_eval_self(net.eval(), build, n=args.eval_games, max_steps=args.eval_steps)
            rm, cl, lets = ds["rally_mean"], ds["shot_mix"]["clear"] + ds["shot_mix"]["lift"], ds["faults_per_rally"]["let"]
            sf, disp = ds["faults_per_rally"]["serve"], ds["disp_mean"]
            gates = wr >= GATE_WR and rm >= GATE_RALLY and sf <= GATE_SERVE_FAULT
            ok = gates and (disp >= GATE_DISP_X * DISP_BASELINE or not is_all6)
            streak = streak + 1 if ok else 0
            print(f"  self-play: rally mean {rm:.2f}, disp {disp:.2f} m ({disp / DISP_BASELINE:.2f}x), serve faults {sf:.3f}, clear+lift {cl:.3f}, "
                  f"lets {lets:.3f}; gates {'ok' if ok else 'not met'} (streak {streak})", flush=True)
            with open(out_dir / "ckpt_eval.jsonl", "a") as f:
                f.write(jdump(dict(min=round(elapsed / 60, 1), wr=wr, share=share, rally_self=rm, clear_lift=cl, lets=lets, serve_fault=sf,
                                        disp=disp, disp_median=ds["disp_median"], opp_run=ds["opp_run_per_shot"], ok=ok)) + "\n")
            # best: tier 2 = bot win rate, rally and serve faults pass; tier 1 = bot win rate only; then the highest displacement
            score = (2 if gates else 1 if wr >= GATE_WR else 0) * 1000 + disp
            if score > best:
                best = score
                save_all("best", elapsed, sched, wr=wr, share=share, rally=rm, disp=disp, serve_fault=sf, clear_lift=cl)
                print("  new best", flush=True)
            save_all(f"ckpt_{int(elapsed // 60):03d}min", elapsed, sched)
            save_latest(elapsed, sched)
            t_ckpt = time.time()
        if done_training or (args.early_stop and streak >= 2):
            break
    save_all("final", elapsed, sched)
    curve.close()
    print(f"done: {out_dir}  (export: oso-arena export {out_dir / 'best'} -o model.safetensors --name \"My Model\")")
    return out_dir


def main(argv=None):
    args = parse_args(argv)
    try:
        run(args)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
