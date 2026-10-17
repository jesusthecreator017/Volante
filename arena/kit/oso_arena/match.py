"""One match: policy A (player 0, -X side) vs policy B (player 1), a single game by the contract's rules (spec 3.4).
Deterministic: the same inputs give an identical MatchLog (and identical bytes from log.write)."""
import numpy as np

from oso_arena import CONTRACT, KIT_VERSION, bot, builds as B, sim
from oso_arena.log import MatchLog
from oso_arena.policy import NumpyPolicy

VOID_S = 150 * 60.0  # a match over 150 simulated play minutes (intervals excluded) is void
INTERVAL_MID_S, INTERVAL_GAME_S, INTERVAL_AT = 60.0, 120.0, 8  # time only: no effect on play


class _Bot:
    meta = dict(name="House Bot", description="the scripted bot", build=B.ALL6, sha256="bot")

    @staticmethod
    def act(obs):
        return bot.bot_actions(obs)


def _player(x):
    if isinstance(x, NumpyPolicy):
        return x
    return _Bot() if isinstance(x, str) and x == "bot" else NumpyPolicy.load(x)


def _annotate(events):
    """Tags events with game number and, on `start` events, the world side of A (`ends`: 0 = -X, 1 = +X) and the interval before the
    rally (s). Ends: game 1 side 0, game 2 side 1, game 3 side 0 and flipped when the leading score first reaches 8. Returns the
    per-game first servers."""
    game, reached, flip, firsts = 1, False, 0, []
    for e in events:
        e["game"] = game
        if e["kind"] == "start":
            first = e["score"] == [0, 0] and (not firsts or len(firsts) < game)
            if first:
                firsts.append(e["s"])
                reached, flip = False, 0
            ivl = INTERVAL_GAME_S if first and game > 1 else 0.0
            if not reached and max(e["score"]) >= INTERVAL_AT:
                reached, ivl = True, INTERVAL_MID_S
                flip = 1 if game == 3 else 0
            e["ends"] = (game - 1) % 2 ^ flip
            e["interval_before_s"] = ivl
        elif e["kind"] == "game_over":
            game += 1
    return firsts


def _rallies(events):
    out, cur = [], None
    for e in events:
        if e["kind"] == "start":
            cur = dict(game=e["game"], ends=e["ends"], interval_before_s=e["interval_before_s"], start_t=e["t"], end_t=None, server=e["s"],
                       winner=None, reason=None, shots=0, score=list(e["score"]), contact_q=[])
        elif e["kind"] == "hit" and cur is not None:
            cur["contact_q"].append(round(e["q"], 4))  # contact quality of each shot of the rally, in order
        elif e["kind"] == "land" and cur is not None:
            cur.update(end_t=e["t"], reason=e["reason"], shots=e["shots"])
            if e["reason"] == "let":
                out.append(cur)
        elif e["kind"] == "point" and cur is not None:
            cur.update(winner=e["winner"], score=list(e["score"]))
            out.append(cur)
    return out


def play(a, b, seed, first_server=None, target=None, max_sim_s=VOID_S, best_of=3):
    """A best-of-`best_of` match (BWF laws, 15-point games: rally scoring, win by 2, cap 21; the winner of a game serves first in the
    next; intervals 60 s at 8 points and 120 s between games; change of ends), or the first to win. a, b: path to a .safetensors
    submission, a loaded NumpyPolicy, or "bot". first_server (game 1) None = drawn from the seed. target overrides the 15 points (the
    smoke test: best_of=1, target=5). max_sim_s: simulated play seconds before the match is void."""
    pa, pb = _player(a), _player(b)
    bl = np.array([[B.to_array(pa.meta["build"]), B.to_array(pb.meta["build"])]])
    s = sim.BadSim(1, seed, builds=bl, log_game=0)
    s.random_server_after_game = False
    if target is not None:
        s.win_points = target
    if first_server is not None:
        s.set_first_server(first_server)
    obs = s._obs()
    wins, scores, voided = [0, 0], [], False
    need = best_of // 2 + 1
    while max(wins) < need:
        act = np.empty((1, 2, sim.ACT_DIM))
        act[:, 0], act[:, 1] = pa.act(obs[:, 0]), pb.act(obs[:, 1])
        if not np.isfinite(act).all():
            raise FloatingPointError("a policy produced a non-finite action")
        obs, _, done, info = s.step(act)
        if done[0]:
            gs = [int(x) for x in info["final_score"][0]]
            scores.append(gs)
            wins[int(gs[1] > gs[0])] += 1
        elif s.t > max_sim_s:
            voided = True
            break
    g = s.serialize_game(0)
    events = g["events"]
    if not voided:
        last = max(i for i, e in enumerate(events) if e["kind"] == "game_over")
        events = events[:last + 1]
    firsts = _annotate(events)
    nf = None if voided else events[-1]["k"] // 4 + 1
    frames = dict(pos=g["pos"][::4][:nf].astype(np.float32), vel=g["vel"][::4][:nf].astype(np.float16),
                  shuttle=g["shuttle"][::4, :3][:nf].astype(np.float32))
    detail = _rallies(events)
    games = [dict(score=sc, winner=int(sc[1] > sc[0]), first_server=firsts[i], rallies=sum(1 for r in detail if r["game"] == i + 1))
             for i, sc in enumerate(scores)]
    duration = float(s.t if voided else events[-1]["t"])
    return MatchLog(
        contract=CONTRACT, kit_version=KIT_VERSION, seed=int(seed), a_sha256=pa.meta["sha256"], b_sha256=pb.meta["sha256"],
        a_name=pa.meta["name"], b_name=pb.meta["name"], builds=[bl[0, 0].tolist(), bl[0, 1].tolist()], score=wins,
        winner=None if voided else int(wins[1] > wins[0]), voided=voided,
        reason=f"over {max_sim_s / 60:g} simulated minutes" if voided else None, rallies=len(detail), duration_s=duration,
        wall_duration_s=duration + sum(r["interval_before_s"] for r in detail), games=games, rallies_detail=detail, frames=frames,
        events=events)
