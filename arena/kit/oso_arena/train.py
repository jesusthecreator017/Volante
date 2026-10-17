"""PPO self-play for the badminton simulator (small own PPO, PyTorch).
  python arena/kit/oso_arena/train.py --minutes 210            # new run (or add --resume to continue out/badsim/latest.pt)
Games: 60 % self-play (one shared policy on both slots), 30 % against a frozen snapshot from a pool (new snapshot every
SNAPSHOT_EVERY samples), 10 % against the scripted bot; the opponent assignment is redrawn every iteration. Reward: +1 / -1 per rally
plus a shaping term for a legal net crossing that is annealed to 0 over the first 30 % of the time budget; curriculum (reach x1.3, ramps to the real value by 40 %; the aim-noise halving was dropped in the tuning round). Checkpoints + a JSON-lines training curve go to out/badsim/ every 15 minutes.
Phase 13.1: also the base-position reward (annealed to 0 over the first 25 %), the depth reward (sim.py), lr 1e-4 -> 3e-5 (the stable
Phase 13 schedule), and best.pt = the checkpoint with the longest self-play rallies among those with bot win rate >= 80 % and
clear + lift >= 10 % of self-play shots (each checkpoint's numbers are appended to ckpt_eval.jsonl).
Phase 13.2: best.pt = highest self-play displacement among bot win rate >= 80 %, rally >= 6, serve faults <= 0.05 (else highest with win rate >= 80 %);
stops early when every gate (incl. displacement >= 1.5x DISP_BASELINE) passes at 2 consecutive checkpoints; update on MPS."""
import argparse
import copy
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from oso_arena import bot, evaluate, sim  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / os.environ.get("BADSIM_OUT", "out/badsim")
N_GAMES, HORIZON = 1024, 64  # Phase 13.2: 256 -> 1024 (profiled: the PPO update dominated; it runs on MPS)
GAMMA, LAMBDA, CLIP, VF_COEF, MAX_GRAD = 0.997, 0.95, 0.2, 0.5, 0.5
EPOCHS, MINIBATCH, TARGET_KL = 4, 16384, 0.03  # same update count per iteration as 256 games / 4096
LR0, LR1 = float(os.environ.get("BADSIM_LR0", 1e-4)), float(os.environ.get("BADSIM_LR1", 3e-5))
ENT0, ENT1 = 6e-3, 2e-4  # tuning round: ENT0 3e-3 -> 6e-3
P_SELF, P_SNAP = 0.6, 0.3  # remaining 0.1: bot
SNAPSHOT_EVERY = 5_000_000  # samples (player-slot steps)
POOL_MAX = 12
LOG_EVERY_S, CKPT_EVERY_S = 120, 600
DEV = torch.device("mps" if torch.backends.mps.is_available() and not os.environ.get("BADSIM_CPU") else "cpu")
HIDDEN = 256


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


def load_net(path):
    sd = torch.load(path, map_location="cpu", weights_only=False)["model"]
    net = ActorCritic(sd["actor.0.weight"].shape[1])
    net.load_state_dict(sd)
    return net.eval()


def np_policy(net, deterministic=True, std_scale=1.0):
    def act(obs):
        with torch.no_grad():
            return net.act(torch.as_tensor(obs, dtype=torch.float32), deterministic, std_scale)[0].numpy()

    return act


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


def quick_stats(net, n=64, steps=1500, seed=0):
    """Short deterministic self-play and policy-vs-bot runs: rally length, faults, shot mix, point win rate vs the bot."""
    pol = np_policy(net)
    out = {}
    for name, opp in (("self", pol), ("bot", bot.bot_actions)):
        s = sim.BadSim(n, seed)
        s.shaping = 0.0
        s.log_shots = True
        obs = s._obs()
        slot = np.arange(n) % 2  # the policy's slot (self-play: both)
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


def full_eval_vs_bot(net, n=64, max_steps=20000):
    r = evaluate.play_games(np_policy(net), bot.bot_actions, n, seed=123, max_steps=max_steps)
    d = evaluate.summarize(r)
    f = r["finished"]
    wr = float((r["a_pts"] > r["b_pts"])[f].mean()) if f.any() else 0.0
    share = float(r["a_pts"].sum() / max((r["a_pts"] + r["b_pts"]).sum(), 1))
    return wr, share, d


GATE_WR, GATE_RALLY, GATE_SERVE_FAULT, GATE_DISP_X = 0.80, 6.0, 0.05, 1.5
DISP_BASELINE = 1.62  # Phase 13.1 best.pt, self-play mean displacement of non-serve shots (evaluate.py --legacy, 200 games)


def full_eval_self(net, n=64, max_steps=20000):
    """Deterministic self-play full games: the evaluate.summarize dict."""
    pol = np_policy(net)
    d = evaluate.summarize(evaluate.play_games(pol, pol, n, seed=124, max_steps=max_steps))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=210.0, help="wall-clock budget (of the whole run when resuming)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--init", type=Path, help="start from these weights (fresh optimizer) ...")
    ap.add_argument("--init-elapsed", type=float, default=0.0, help="... with the schedules (lr, entropy, reach) at this many minutes")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=max(os.cpu_count() - 2, 1))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    net = ActorCritic()
    gnet = ActorCritic().to(DEV)  # the PPO update runs here (MPS); rollouts use the CPU copy `net`
    opt = torch.optim.Adam(gnet.parameters(), lr=LR0, eps=1e-5)
    pool, elapsed, samples, it, next_snap, best = [], 0.0, 0, 0, SNAPSHOT_EVERY, -1.0  # best = tier * 1000 + displacement of best.pt
    if args.resume and (OUT / "latest.pt").exists():
        ck = torch.load(OUT / "latest.pt", map_location="cpu", weights_only=False)
        net.load_state_dict(ck["model"])
        gnet.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])
        pool, elapsed, samples, it, next_snap, best = ck["pool"], ck["elapsed"], ck["samples"], ck["it"], ck["next_snap"], float(ck["best"])
        print(f"resumed at {elapsed / 60:.1f} min, {samples / 1e6:.1f} M samples")
    if args.init and not (args.resume and (OUT / "latest.pt").exists()):
        sd = torch.load(args.init, map_location="cpu", weights_only=False)["model"]
        net.load_state_dict(sd)
        gnet.load_state_dict(sd)
        elapsed = args.init_elapsed * 60
    budget = args.minutes * 60
    game = sim.BadSim(N_GAMES, args.seed + 1000 * it)
    obs = game._obs()
    n, O, A, T = N_GAMES, sim.OBS_DIM, sim.ACT_DIM, HORIZON
    snap_nets = []
    streak = 0  # consecutive checkpoints that pass every gate

    def make_snap_nets():
        snap_nets.clear()
        for sd in pool:
            m = ActorCritic()
            m.load_state_dict(sd)
            snap_nets.append(m.eval())

    make_snap_nets()
    t_run0 = time.time() - elapsed
    t_log = t_ckpt = time.time()
    win = dict(samples=0, bot_w=0, bot_l=0, rallies=0, shots=0, faults=Counter(), t=time.time(), loss=[])
    curve = open(OUT / "train_log.jsonl", "a")

    while True:
        elapsed = time.time() - t_run0
        prog = min(elapsed / budget, 1.0)
        # --- schedules
        game.shaping = 0.1 * max(0.0, 1.0 - prog / 0.3)
        c = min(prog / 0.4, 1.0)
        game.base_scale = 1.0  # Phase 13.2 tuning round: no anneal (annealing it to 0 at 25 % collapsed the game into net camping)
        game.noise_scale, game.reach_scale = 1.0, 1.3 - 0.3 * c  # tuning round: real aim noise from the start
        lr = LR0 + (LR1 - LR0) * prog
        for g_ in opt.param_groups:
            g_["lr"] = lr
        ent = ENT0 + (ENT1 - ENT0) * prog
        # --- opponents for this iteration
        r = rng.random(n)
        otype = np.where(r < P_SELF, 0, np.where(r < P_SELF + P_SNAP, 1, 2))
        if not snap_nets:
            otype[otype == 1] = 0
        lslot = rng.integers(0, 2, n)  # the learner's slot in non-self games
        snap_id = rng.integers(0, max(len(snap_nets), 1), n)
        learn = np.ones((n, 2), bool)
        for p in (0, 1):
            learn[:, p] = (otype == 0) | (lslot == p)
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
            B_rew[t], B_done[t] = rew, done
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
        with torch.no_grad():
            last_val = net.value(torch.as_tensor(obs.reshape(-1, O))).numpy().reshape(n, 2)
        adv, ret = gae(B_rew, B_val, last_val, B_done)
        m = np.broadcast_to(learn[None], (T, n, 2))
        X = torch.as_tensor(B_obs[m]).to(DEV)
        Y = dict(act=torch.as_tensor(B_act[m]), lp=torch.as_tensor(B_lp[m]), adv=torch.as_tensor(adv[m]), ret=torch.as_tensor(ret[m]))
        Y = {k_: v_.to(DEV) for k_, v_ in Y.items()}
        Y["adv"] = (Y["adv"] - Y["adv"].mean()) / (Y["adv"].std() + 1e-8)
        k = len(X)
        samples += k
        win["samples"] += k
        # --- PPO update
        stop = False
        for _ in range(EPOCHS):
            perm = torch.randperm(k, device=DEV)
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
            pool.append(copy.deepcopy(net.state_dict()))
            pool[:] = pool[-POOL_MAX:]
            make_snap_nets()
            next_snap += SNAPSHOT_EVERY
        now = time.time()
        done_training = elapsed >= budget
        if now - t_log >= LOG_EVERY_S or done_training:
            q = quick_stats(net.eval())
            sps = win["samples"] / (now - win["t"])
            wl = win["bot_w"] + win["bot_l"]
            row = dict(min=round(elapsed / 60, 1), it=it, msamples=round(samples / 1e6, 1), sps=int(sps), prog=round(prog, 3),
                       shaping=round(game.shaping, 3), base=round(game.base_scale, 2), noise=round(game.noise_scale, 2), reach=round(game.reach_scale, 2),
                       train_selfplay_rally=round(win["shots"] / max(sum(win["faults"][k] for k in sim.REASONS[:-1]), 1), 2),
                       train_bot_point_wr=round(win["bot_w"] / max(wl, 1), 3), pg=round(float(np.mean([l[0] for l in win["loss"]])), 4),
                       vl=round(float(np.mean([l[1] for l in win["loss"]])), 4), std=round(float(net.log_std.exp().mean()), 3),
                       eval=q, pool=len(pool))
            print(json.dumps(row), flush=True)
            curve.write(json.dumps(row) + "\n")
            curve.flush()
            win.update(samples=0, bot_w=0, bot_l=0, rallies=0, shots=0, faults=Counter(), t=now, loss=[])
            t_log = now
        if now - t_ckpt >= CKPT_EVERY_S or done_training:
            torch.save(dict(model=net.state_dict(), opt=opt.state_dict(), pool=pool, elapsed=elapsed, samples=samples, it=it,
                            next_snap=next_snap, best=best), OUT / "latest.pt")
            wr, share, d = full_eval_vs_bot(net.eval())
            print(f"checkpoint @ {elapsed / 60:.1f} min: full games vs bot: win rate {wr:.3f}, point share {share:.3f}, "
                  f"rally mean {d['rally_mean']:.1f}", flush=True)
            ds = full_eval_self(net.eval())
            rm, cl, lets = ds["rally_mean"], ds["shot_mix"]["clear"] + ds["shot_mix"]["lift"], ds["faults_per_rally"]["let"]
            sf, disp = ds["faults_per_rally"]["serve"], ds["disp_mean"]
            gates = wr >= GATE_WR and rm >= GATE_RALLY and sf <= GATE_SERVE_FAULT
            ok = gates and disp >= GATE_DISP_X * DISP_BASELINE
            streak = streak + 1 if ok else 0
            print(f"  self-play: rally mean {rm:.2f}, disp {disp:.2f} m ({disp / DISP_BASELINE:.2f}x), serve faults {sf:.3f}, clear+lift {cl:.3f}, "
                  f"lets {lets:.3f}; gates {'ok' if ok else 'not met'} (streak {streak})", flush=True)
            with open(OUT / "ckpt_eval.jsonl", "a") as f:
                f.write(json.dumps(dict(min=round(elapsed / 60, 1), wr=wr, share=share, rally_self=rm, clear_lift=cl, lets=lets, serve_fault=sf,
                                        disp=disp, disp_median=ds["disp_median"], opp_run=ds["opp_run_per_shot"], ok=ok)) + "\n")
            # best.pt: tier 2 = bot win rate, rally and serve faults pass; tier 1 = bot win rate only; then the highest displacement
            score = (2 if gates else 1 if wr >= GATE_WR else 0) * 1000 + disp
            if score > best:
                best = score
                torch.save(dict(model=net.state_dict(), wr=wr, share=share, rally=rm, disp=disp, serve_fault=sf, clear_lift=cl, elapsed=elapsed), OUT / "best.pt")
                print("  new best", flush=True)
            torch.save(dict(model=net.state_dict(), elapsed=elapsed), OUT / f"ckpt_{int(elapsed // 60):03d}min.pt")
            torch.save(dict(model=net.state_dict(), opt=opt.state_dict(), pool=pool, elapsed=elapsed, samples=samples, it=it,
                            next_snap=next_snap, best=best), OUT / "latest.pt")
            t_ckpt = time.time()
            t_log = max(t_log, 0)
        if done_training or streak >= 2:
            break
    torch.save(dict(model=net.state_dict(), elapsed=elapsed), OUT / "final.pt")
    print("done")


if __name__ == "__main__":
    main()
