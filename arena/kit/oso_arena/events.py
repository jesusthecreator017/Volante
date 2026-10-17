"""What happened in one decision step (30 Hz), for reward functions: `StepEvents`, built on demand by `BadSim.events()` after a
`step`. Read-only numpy arrays, one row per game; the simulator only records, it never turns events into reward
(see `oso_arena.rewards`).

A decision step is S = 4 simulator steps of 1/120 s. Hits and net crossings carry a second axis, the simulator step `k` of the
decision step they happened in ([n, S] arrays: `ev.hits.mask[:, k]`; most are False, two hits or crossings of one game inside 33 ms
do happen). Use `.any(1)` for "did it happen in this step". Landings and rally ends happen at most once per game and step ([n]).
Players are 0 / 1 by court slot: player 0 is on the -X half, player 1 on +X. Coordinates are world coordinates (net at x = 0,
+Y up, z across, metres).
"""
from dataclasses import dataclass

import numpy as np

# rally-end reasons (index into the `reason` arrays; the Pi and the match log use the names)
NET, OUT, MISSED, SERVE, NET_TOUCH, CEILING, LET = range(7)
REASONS = ["net", "out", "missed", "serve", "net_touch", "ceiling", "let"]


@dataclass(frozen=True)
class Hits:
    """Racquet hits in this step (serves included); every array has the axes [n_games, S], then the trailing shape shown."""
    mask: np.ndarray        # bool, a hit happened
    player: np.ndarray      # int, who hit (-1 where there was no hit)
    contact: np.ndarray     # [.., 3] shuttle position at the hit (x, y, z)
    speed: np.ndarray       # launch speed (m/s), after the power cap and contact quality
    angle: np.ndarray       # launch elevation (degrees above horizontal)
    land: np.ndarray        # [.., 2] predicted landing point (world x, z), the aim after noise
    flight_time: np.ndarray  # predicted flight time (s)
    quality: np.ndarray     # contact quality 0-1
    disp: np.ndarray        # distance from the opponent (at the hit) to the predicted landing point (m)
    shot_no: np.ndarray     # shot number in the rally (1 = the serve)
    extended: np.ndarray    # bool, a lunge or a jump
    mishit: np.ndarray      # bool, a frame hit


@dataclass(frozen=True)
class Crossings:
    """Legal crossings of the net (over the tape, not a net fault), values as they were at the crossing. Axes [n_games, S] (+ trailing)."""
    mask: np.ndarray        # bool
    player: np.ndarray      # int, the hitter (-1 none)
    land: np.ndarray        # [.., 2] the hit's predicted landing point (world x, z)
    disp: np.ndarray        # displacement of that hit (see Hits.disp)
    shots: np.ndarray       # shots played in the rally so far


@dataclass(frozen=True)
class Landings:
    """The shuttle came down on the floor this step (the end of a rally that was not a net fault, a ceiling hit or a let)."""
    mask: np.ndarray        # [n] bool
    xz: np.ndarray          # [n, 2] where it touched the floor (world x, z)
    hitter: np.ndarray      # [n] int, the last player to hit it (-1 none)
    in_court: np.ndarray    # [n] bool, inside the singles court (lines are in)


@dataclass(frozen=True)
class RallyEnd:
    """A rally ended in this step."""
    mask: np.ndarray        # [n] bool
    winner: np.ndarray      # [n] int, 0 / 1, or -1 for a let (a 150-shot rally is replayed, nobody scores)
    reason: np.ndarray      # [n] int, index into REASONS
    shots: np.ndarray       # [n] shots played in the rally


@dataclass(frozen=True)
class StepEvents:
    """Everything a reward function may look at. `pos`, `builds`, `last_hitter` and `shots` are the state at the end of the step
    (after a rally ended and the next one was set up, they describe the new rally)."""
    n_games: int
    hits: Hits
    crossings: Crossings
    landings: Landings
    rally_end: RallyEnd
    pos: np.ndarray          # [n, 2, 2] both players' positions (world x, z)
    builds: np.ndarray       # [n, 2, 4] (power, speed, reach, accuracy) per player
    last_hitter: np.ndarray  # [n] who hit the shuttle last
    shots: np.ndarray        # [n] shots played in the current rally
    server: np.ndarray       # [n] who serves the current rally


def _ro(a):
    a = np.array(a)  # own copy: nothing the simulator keeps changing may leak into the event
    a.setflags(write=False)
    return a


SUBSTEPS = 4  # simulator steps per decision step (sim.DECISION)


def empty_buffers(n):
    """Writable per-step buffers the simulator fills in (turned into a StepEvents by `freeze`)."""
    S = SUBSTEPS
    return dict(
        hit=np.zeros((n, S), bool), hit_player=np.full((n, S), -1), hit_contact=np.zeros((n, S, 3)), hit_speed=np.zeros((n, S)),
        hit_angle=np.zeros((n, S)), hit_land=np.zeros((n, S, 2)), hit_time=np.zeros((n, S)), hit_q=np.zeros((n, S)), hit_disp=np.zeros((n, S)),
        hit_shot=np.zeros((n, S), int), hit_ext=np.zeros((n, S), bool), hit_mis=np.zeros((n, S), bool),
        cross=np.zeros((n, S), bool), cross_player=np.full((n, S), -1), cross_land=np.zeros((n, S, 2)), cross_disp=np.zeros((n, S)),
        cross_shots=np.zeros((n, S), int),
        land=np.zeros(n, bool), land_xz=np.zeros((n, 2)), land_hitter=np.full(n, -1), land_in=np.zeros(n, bool),
        end=np.zeros(n, bool), end_winner=np.full(n, -1), end_reason=np.zeros(n, int), end_shots=np.zeros(n, int))


def freeze(b, pos, builds, last_hitter, shots, server):
    n = len(b["hit"])
    return StepEvents(
        n_games=n,
        hits=Hits(_ro(b["hit"]), _ro(b["hit_player"]), _ro(b["hit_contact"]), _ro(b["hit_speed"]), _ro(b["hit_angle"]), _ro(b["hit_land"]),
                  _ro(b["hit_time"]), _ro(b["hit_q"]), _ro(b["hit_disp"]), _ro(b["hit_shot"]), _ro(b["hit_ext"]), _ro(b["hit_mis"])),
        crossings=Crossings(_ro(b["cross"]), _ro(b["cross_player"]), _ro(b["cross_land"]), _ro(b["cross_disp"]), _ro(b["cross_shots"])),
        landings=Landings(_ro(b["land"]), _ro(b["land_xz"]), _ro(b["land_hitter"]), _ro(b["land_in"])),
        rally_end=RallyEnd(_ro(b["end"]), _ro(b["end_winner"]), _ro(b["end_reason"]), _ro(b["end_shots"])),
        pos=_ro(pos), builds=_ro(builds), last_hitter=_ro(last_hitter), shots=_ro(shots), server=_ro(server))
