"""Vectorised singles badminton simulator (numpy, N games at once). Spec: .claude/specs/phase13-ai-badminton.md.

Court coordinates as everywhere else (oso_arena/court.py): net at x = 0, +Y up, z across; player 0 on the -X half, player 1 on +X.
Net height is the centre value (1.524 m) everywhere. Shuttle: oso_arena/physics.py drag model (terminal velocity 6.8 m/s),
integrated with midpoint RK2 at 1/120 s (error vs the RK4 truth < 2 mm over a 2 s clear; test_badsim.py checks 2 cm).
The policy acts at 30 Hz: `step` holds the action for 4 sim steps.

Phase 14.2b: singles laws (see CONTRACT.md section 6), a net-cord model, a shuttle turnover phase after every hit and physical racquet
contact (contact quality from the shuttle's position in the hitter's body frame scales the launch cap and the aim noise).

Shots: the action's target landing point (normalised over the opponent's half; for the serve over the diagonal service court, so
a policy cannot get stuck always serving into the wrong court) and flight-time parameter u are turned into a launch velocity by a lookup table
(distance, contact height, flight time) -> (horizontal speed, vertical speed, apex), built once with a vectorised Newton solve on
the same integrator and cached in out/badsim/. A drag flight stays in the vertical plane through its start and target, so 3 inputs
are enough. Noise is added to the aim point before the second lookup.
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the kit root, so `python sim.py` finds oso_arena
from oso_arena import builds as B  # noqa: E402
from oso_arena import court as C  # noqa: E402
from oso_arena import physics as sp  # noqa: E402

DT = 1 / 120
DECISION = 4  # sim steps per policy decision (30 Hz)
K_DRAG = sp.G / sp.VT**2
NET_H = C.NET_TOP_CENTER
HL, HW = C.HALF_LENGTH, C.SINGLES_HALF_WIDTH
SHORT_X = C.SHORT_SERVICE_X
SIGN = np.array([-1.0, 1.0])  # x side of player p
MIRROR = np.array([1.0, -1.0])  # world -> own view: (x, z) *= MIRROR[p] (an involution)

# Per-player limits (speed, acceleration, reach, launch-speed cap, aim noise) come from the stat build, see builds.py / CONTRACT.md. The
# all-6 build is the Phase 13.2 set: V_MAX 5.0, A_MAX 10.0, REACH 1.5, REACH_LUNGE 2.0 (13.1 tuning round: 1.0 / 1.4 made smashes
# unreturnable, 1.3 / 1.8 still gave no clears), launch cap 25 m/s below 1.0 m rising to 90 m/s at >= 2.6 m.
PT = B.param_table()  # {param: [11]} by stat value
H_MIN, H_MAX, H_MAX_JUMP = 0.15, 2.9, 3.2
FREEZE_LUNGE, FREEZE_JUMP = 0.25, 0.3
MIN_HIT_GAP = 0.35
X_MIN_P, X_MAX_P, Z_MAX_P = 0.4, 7.5, 3.6  # a player's allowed |x| range (never at the net) and |z|
SERVER_X, RECEIVER_X, SERVICE_Z = 2.6, 3.6, 1.3
TOSS_AHEAD, TOSS_Y = 0.9, 1.1
WIN_POINTS, WIN_BY, CAP_POINTS = 15, 2, 21
# Amendment 2026-10-16 (longer rallies): a small per-shot reward kept for the whole run (not annealed, always less than a point) and an
# extra aim noise that grows with the square of the launch speed, so fast smashes miss more often than drops and clears.
SHOT_REWARD, SHOT_REWARD_MAX_SHOTS = 0.0, 10  # Phase 13.2: off (it paid both sides for a cooperative rally); code path kept. Cap: first 10 shots
SMASH_NOISE = 0.35  # m, sigma += SMASH_NOISE * (|v0| / 90)^2
# Phase 13.1: depth reward, paid at the legal net crossing (so a net fault earns nothing) from the hit's predicted landing
# point (self.land): DEPTH_REWARD * clip((|x| - SHORT_X) / (HL - SHORT_X), 0, 1), 0 when that point is out. Same first-10-shots
# cap as SHOT_REWARD (10 rally shots = 5 per player), so (SHOT + DEPTH) * 5 = 0.9 < 1 point per player.
DEPTH_REWARD = 0.05  # Phase 13.2: 0.15 -> 0.05 (displacement is now the main term)
# Phase 13.2: displacement reward at the same point and under the same cap: DISP_REWARD * clip(d / DISP_NORM, 0, 1), d = horizontal
# distance from the opponent's position at the moment of the hit to the shot's predicted landing point. Not annealed.
# Per player <= 5 counted shots: 5 * (DISP_REWARD + DEPTH_REWARD) = 0.75 < 1.
DISP_REWARD, DISP_NORM = 0.10, 4.0
REACTION_S = 0.20  # after the opponent's hit a player keeps their velocity (commands ignored) for this long
SERVE_FAULT_PENALTY = 0.5  # extra loss for the server on a serve fault (on top of the lost point)
# Base-position reward: while the opponent is the next hitter (the player was the last hitter), each decision step pays
# BASE_REWARD * exp(-d^2 / (2 * 0.8^2)), d = distance to (|x| = BASE_X, z = 0) on the own half. ~0.001 x ~70 steps (a 5-shot rally
# is ~2.5 s of waiting per player at 30 Hz) = ~0.07 per rally. Annealed to 0 by train.py (BadSim.base_scale).
BASE_REWARD, BASE_X, BASE_SIGMA = 0.001, 3.8, 0.8
RALLY_CAP_SHOTS = 150  # a longer rally is replayed (a "let"), nobody scores

# Phase 14.2b shuttle physics. Drag: sim steps use physics.py's model (terminal velocity 6.8 m/s, fitted to video, tools/shuttle_physics.py).
# Turnover: a struck shuttle starts skirt first and flips cork first within a few ms; modelled (assumed values, not fitted) as 30 ms of
# extra drag, x1.6 decaying linearly to x1.0.
TURNOVER_S, TURNOVER_DRAG = 0.03, 1.6
# Net tape: BWF Law 1.9: tape 75 mm wide folded over the cord, top 1.524 m at the centre and 1.55 m at the posts. Modelled as a horizontal
# cylinder along z of radius TAPE_R; the shuttle (cork radius SHUTTLE_R) touches it when its centre is within CORD_R of the tape axis.
TAPE_R, SHUTTLE_R = 0.02, 0.03
CORD_R = TAPE_R + SHUTTLE_R
CORD_E, CORD_TANGENT, CORD_SIGMA = 0.15, 0.5, 0.2  # normal restitution, tangential damping, x-velocity deflection sigma (m/s); a soft, slack tape
CEILING_Y, HALL_X, HALL_Z = 11.5, 12.0, 9.5  # hall ceiling (crates/oso-render hall.rs) and the tighter of its walls around our court
NET_TOUCH_X = 0.10  # a lunging / jumping hit this close to the net plane touches the net (approximation of Law 13.4.3)
SERVE_MAX_H = 1.15  # BWF Law 9.1.3: the whole shuttle below the server's waist, ~1.15 m (we use the shuttle centre)
# Racquet contact (CONTRACT.md "Contact quality"). Body frame of the hitter: f forward (toward the net), l lateral (racquet side +),
# y height. The ideal zone is in front of the right shoulder; the zone's f minimum relaxes to ZONE_F_OVERHEAD at ZONE_OVERHEAD_Y and
# above (an overhead stroke is hit above the head, not out in front).
ZONE_F, ZONE_L, ZONE_Y = (0.2, 0.7), (0.2, 0.9), (0.9, 2.9)
ZONE_F_OVERHEAD, ZONE_OVERHEAD_Y = -0.1, (2.0, 2.6)
LOSS_ZONE, ZONE_SPAN = 0.6, 0.8  # loss = 0.6 * min(1, distance outside the zone / (0.8 * reach))
CRAMPED_R, LOSS_CRAMPED = 0.35, 0.25  # closer than 0.35 m to the body (horizontally): up to 0.25
LATE_F, LOSS_LATE = 0.5, 0.4  # behind the body: 0.4 at 0.5 m behind
BACKHAND_L, LOSS_BACKHAND = 0.4, 0.2  # non-racquet side: 0.2 at 0.4 m
LOSS_STRETCH = 0.15  # lunge or jump
Q_SPEED = (0.6, 0.4)  # launch cap x (0.6 + 0.4 q)
Q_NOISE = 1.5  # aim sigma x (1 + 1.5 (1 - q))
MISHIT_Q, MISHIT_SPEED, MISHIT_SIGMA = 0.15, 0.4, np.radians(25.0)  # frame hit: 40 % of the intended speed, direction error sigma 25 deg

# fault reasons (indices into stats["faults"]; the Pi and the match log use the names)
NET, OUT, MISSED, SERVE, NET_TOUCH, CEILING, LET = range(7)
REASONS = ["net", "out", "missed", "serve", "net_touch", "ceiling", "let"]
ACT_DIM, OBS_DIM = 6, 37  # 29 Phase 13.2 floats + own build (4) + opponent build (4)
OBS_BASE = 29


def drag_factor(tau):
    """Drag multiplier tau seconds after a hit: TURNOVER_DRAG, decaying linearly to 1 over TURNOVER_S (the shuttle flies skirt first)."""
    return 1.0 + (TURNOVER_DRAG - 1.0) * np.clip(1.0 - np.asarray(tau, float) / TURNOVER_S, 0.0, 1.0)


def accel(v, kd=1.0):
    """Gravity plus quadratic drag; kd (scalar or one per row of v) multiplies the drag."""
    kd = np.asarray(kd, float)
    return np.array([0.0, -sp.G, 0.0]) - K_DRAG * (kd[..., None] if kd.ndim else kd) * np.linalg.norm(v, axis=-1, keepdims=True) * v


def rk2(p, v, tau=None):
    """One midpoint step of the drag flight; p, v [..., 3]. tau = time since the last hit (scalar or per row; None = no turnover)."""
    k1, k2 = (1.0, 1.0) if tau is None else (drag_factor(tau), drag_factor(np.asarray(tau) + 0.5 * DT))
    vm = v + 0.5 * DT * accel(v, k1)
    return p + DT * vm, v + DT * accel(vm, k2)


def max_launch_speed(h, lo=25.0, hi=90.0):
    """lo below 1.0 m of contact height, rising linearly to hi at >= 2.6 m (defaults: the all-6 build)."""
    return np.interp(h, [1.0, 2.6], [lo, hi])


def launch_cap(h, power):
    """max_launch_speed for arrays h and the hitters' power stats (same shape)."""
    cap = np.empty(np.shape(h))
    for v in np.unique(power):
        m = power == v
        cap[m] = max_launch_speed(h[m], PT["launch_lo"][v], PT["launch_hi"][v])
    return cap


# ---------------------------------------------------------------- shot table
D_GRID = np.linspace(0.1, 15.0, 75)
H_GRID = np.linspace(0.1, 3.3, 33)
T_STEPS = np.arange(12, 361, 6)  # flight times in sim steps (0.1 .. 3.0 s)
T_GRID = T_STEPS * DT
TABLE_VERSION = 3  # 3: turnover drag in the flights the table solves


def _flight(vh, vy, h0, n):
    """x, y after n steps and the apex of a planar flight from (0, h0); vectors."""
    p = np.stack([np.zeros_like(vh), h0, np.zeros_like(vh)], -1)
    v = np.stack([vh, vy, np.zeros_like(vh)], -1)
    apex = p[..., 1].copy()
    for i in range(n):
        p, v = rk2(p, v, i * DT)  # i * DT = time since the hit
        np.maximum(apex, p[..., 1], out=apex)
    return p[..., 0], p[..., 1], apex


def _build_table():
    """[nD, nH, nT, 3] = horizontal speed, vertical speed, apex height of the flight from (0, h0) that reaches (D, 0) in T."""
    Dm, Hm = np.meshgrid(D_GRID, H_GRID, indexing="ij")
    D, H = Dm.ravel(), Hm.ravel()
    out = np.full((len(D), len(T_GRID), 3), np.nan)
    prev = None
    eps = 1e-4
    for k in range(len(T_GRID) - 1, -1, -1):
        n, T = int(T_STEPS[k]), T_GRID[k]
        guess = np.stack([D / T, (0.5 * sp.G * T * T - H) / T], -1)  # drag-free
        x = guess if prev is None else np.where(np.isfinite(prev[:, :1]), prev[:, :2], guess)
        for _ in range(25):
            vh = np.concatenate([x[:, 0], x[:, 0] + eps, x[:, 0]])
            vy = np.concatenate([x[:, 1], x[:, 1], x[:, 1] + eps])
            xe, ye, _a = _flight(vh, vy, np.tile(H, 3), n)
            m = len(D)
            r = np.stack([xe[:m] - D, ye[:m]], -1)
            if np.nanmax(np.abs(r)) < 1e-8:
                break
            J = np.empty((m, 2, 2))
            J[:, 0, 0], J[:, 1, 0] = (xe[m:2 * m] - xe[:m]) / eps, (ye[m:2 * m] - ye[:m]) / eps
            J[:, 0, 1], J[:, 1, 1] = (xe[2 * m:] - xe[:m]) / eps, (ye[2 * m:] - ye[:m]) / eps
            with np.errstate(all="ignore"):
                dx = -np.linalg.solve(J + 1e-12 * np.eye(2), r[..., None])[..., 0]
            dx = np.nan_to_num(dx, nan=0.0)
            cap = np.maximum(1.0, 0.5 * np.linalg.norm(x, axis=-1))
            nr = np.maximum(np.linalg.norm(dx, axis=-1), 1e-12)
            x = x + dx * np.minimum(1, cap / nr)[:, None]
        xe, ye, apex = _flight(x[:, 0], x[:, 1], H, n)
        ok = (np.abs(xe - D) < 1e-5) & (np.abs(ye) < 1e-5) & np.isfinite(x).all(-1)
        res = np.where(ok[:, None], np.concatenate([x, apex[:, None]], -1), np.nan)
        out[:, k] = res
        prev = res
    return out.reshape(len(D_GRID), len(H_GRID), len(T_GRID), 3)


def _ballistic_fill(table):
    """Cells that did not converge (flights too fast to be feasible) get the drag-free solution, so interpolation near the
    feasible edge stays finite; every such shot is above the launch speed cap and is clamped anyway."""
    D, H, T = np.meshgrid(D_GRID, H_GRID, T_GRID, indexing="ij")
    fill = np.stack([D / T, (0.5 * sp.G * T * T - H) / T, H + 0.0 * T], -1)
    return np.where(np.isfinite(table), table, fill)


def _tmin_table(table, power=6):
    """[nD, nH]: the shortest flight time whose launch speed is within the cap (of that power stat) for that contact height (the
    fastest allowed flight). Where no time is feasible: the time of the slowest launch (the shot is clamped and falls short)."""
    sp0 = np.hypot(table[..., 0], table[..., 1])
    sp0 = np.where(np.isfinite(table[..., 0]), sp0, np.inf)
    cap = max_launch_speed(H_GRID, PT["launch_lo"][power], PT["launch_hi"][power])[None, :, None]
    ok = sp0 <= cap
    first = np.argmax(ok, axis=-1)
    anyok = ok.any(-1)
    k = np.clip(first, 1, len(T_GRID) - 1)
    i, j = np.indices(first.shape)
    s0, s1 = sp0[i, j, k - 1], sp0[i, j, k]
    c = cap[0, :, 0][None, :]
    with np.errstate(all="ignore"):
        f = np.where(np.isfinite(s0), (s0 - c) / (s0 - s1), 1.0)
    f = np.clip(np.nan_to_num(f, nan=1.0), 0, 1)
    t_cross = np.where(first == 0, T_GRID[0], T_GRID[k - 1] + f * (T_GRID[k] - T_GRID[k - 1]))
    return np.where(anyok, t_cross, T_GRID[np.argmin(sp0, axis=-1)])


class ShotTable:
    def __init__(self, cache=True):
        path = _cache_path()
        raw = None
        if cache and path.exists():
            try:
                z = np.load(path, allow_pickle=False)
                if int(z["version"]) == TABLE_VERSION:
                    raw = z["table"]
            except (OSError, ValueError, KeyError):
                raw = None
        if raw is None:
            raw = _build_table()
            if cache:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    np.savez(path, table=raw, version=TABLE_VERSION)
                except OSError:
                    pass  # read-only install: rebuilt next time
        self.raw = raw
        self.table = _ballistic_fill(raw)
        self._tmin = {}  # power stat -> [nD, nH], built on first use

    def tmin_for(self, power):
        if power not in self._tmin:
            self._tmin[power] = _tmin_table(self.raw, power)
        return self._tmin[power]

    @property
    def tmin(self):
        return self.tmin_for(6)

    @staticmethod
    def _coords(grid, x):
        f = (np.clip(x, grid[0], grid[-1]) - grid[0]) / (grid[1] - grid[0])
        i = np.minimum(f.astype(int), len(grid) - 2)
        return i, f - i

    def t_min(self, D, h, power=None):
        """Fastest allowed flight time; `power` (array of the hitters' power stats) picks the launch cap, default all-6."""
        i, a = self._coords(D_GRID, D)
        j, b = self._coords(H_GRID, h)
        if power is None:
            power = np.full(np.shape(D), 6)
        out = np.empty(np.shape(D))
        for v in np.unique(power):
            m = power == v
            t = self.tmin_for(int(v))
            am, bm, im, jm = a[m], b[m], i[m], j[m]
            out[m] = ((1 - am) * (1 - bm) * t[im, jm] + am * (1 - bm) * t[im + 1, jm] + (1 - am) * bm * t[im, jm + 1]
                      + am * bm * t[im + 1, jm + 1])
        return out

    def lookup(self, D, h, T):
        """(horizontal speed, vertical speed, apex) arrays for arrays D (m), h (m), T (s)."""
        i, a = self._coords(D_GRID, D)
        j, b = self._coords(H_GRID, h)
        k, c = self._coords(T_GRID, T)
        t = self.table
        out = 0.0
        for di, wa in ((0, 1 - a), (1, a)):
            for dj, wb in ((0, 1 - b), (1, b)):
                for dk, wc in ((0, 1 - c), (1, c)):
                    out = out + (wa * wb * wc)[:, None] * t[i + di, j + dj, k + dk]
        return out[:, 0], out[:, 1], out[:, 2]


def _cache_path():
    """Shot-table cache: $OSO_ARENA_CACHE, else out/badsim in a repo checkout, else ~/.cache/oso-arena."""
    env = os.environ.get("OSO_ARENA_CACHE")
    if env:
        return Path(env) / "shot_table.npz"
    root = Path(__file__).resolve().parents[3]
    return (root / "out/badsim" if (root / "Cargo.toml").exists() else Path.home() / ".cache/oso-arena") / "shot_table.npz"


_TABLE = None


def shot_table():
    global _TABLE
    if _TABLE is None:
        _TABLE = ShotTable()
    return _TABLE


def toss_time():
    p, v, n = np.array([0.0, TOSS_Y, 0.0]), np.zeros(3), 0
    while p[1] > 0:
        p, v = rk2(p, v)
        n += 1
    return n * DT


T_TOSS = toss_time()


def is_over(s, o, win=WIN_POINTS):
    """15 points win by 2, capped at 21 (`win` is lowered only by the kit's smoke test)."""
    return (s >= win and s - o >= WIN_BY) | (s >= CAP_POINTS)


def tape_height(z):
    """Net tape top at z: 1.524 m at the centre, 1.55 m at the posts (|z| = 3.05), linear."""
    return NET_H + (C.NET_TOP_POST - NET_H) * np.clip(np.abs(z) / C.HALF_WIDTH, 0.0, 1.0)


def contact_quality(f, l, y, reach, extended):
    """Contact quality q in [0, 1] of a racquet hit with the shuttle at (f forward, l lateral, y height) in the hitter's body frame
    (arrays). 1 inside the ideal zone, lowered by: distance outside the zone, cramped (< CRAMPED_R from the body), late (f < 0),
    backhand (l < 0), stretched (`extended`: a lunge or jump). `reach` is the hitter's normal reach (m)."""
    f_lo = ZONE_F[0] + (ZONE_F_OVERHEAD - ZONE_F[0]) * np.clip((y - ZONE_OVERHEAD_Y[0]) / (ZONE_OVERHEAD_Y[1] - ZONE_OVERHEAD_Y[0]), 0, 1)
    d = np.sqrt(np.maximum(f_lo - f, 0) ** 2 + np.maximum(f - ZONE_F[1], 0) ** 2 + np.maximum(ZONE_L[0] - l, 0) ** 2
                + np.maximum(l - ZONE_L[1], 0) ** 2 + np.maximum(ZONE_Y[0] - y, 0) ** 2 + np.maximum(y - ZONE_Y[1], 0) ** 2)
    loss = (LOSS_ZONE * np.minimum(1.0, d / (ZONE_SPAN * reach))
            + LOSS_CRAMPED * np.clip(1.0 - np.hypot(f, l) / CRAMPED_R, 0, 1)
            + LOSS_LATE * np.clip((-f - np.maximum(-f_lo, 0)) / LATE_F, 0, 1)  # an overhead stroke may be hit slightly behind
            + LOSS_BACKHAND * np.clip(-l / BACKHAND_L, 0, 1)
            + LOSS_STRETCH * extended)
    return np.clip(1.0 - loss, 0.0, 1.0)


def in_bounds(x, z):
    """Singles court, lines are in."""
    return (np.abs(x) <= HL) & (np.abs(z) <= HW)


def in_service_court(x, z, z_sign):
    """Diagonal singles service court on the side z_sign (+1 / -1): short service line to the back line, centre line to side line."""
    return (np.abs(x) >= SHORT_X) & (np.abs(x) <= HL) & (z * z_sign >= 0) & (np.abs(z) <= HW)


def service_z_sign(server, score_server):
    """z side the server stands on: right court on an even score. The right of a player facing the net is +z for player 0
    (on -X, facing +X) and -z for player 1."""
    right = -SIGN[server]
    return np.where(score_server % 2 == 0, right, -right)


class BadSim:
    def __init__(self, n_games, seed=0, builds=None, log_game=None):
        """builds: int array [n_games, 2, 4] (power, speed, reach, accuracy per player) or None for the all-6 build."""
        self.n = n_games
        self.rng = np.random.default_rng(seed)
        self.table = shot_table()
        self.noise_scale = 1.0  # curriculum knobs
        self.reach_scale = 1.0
        self.base_scale = 1.0  # annealed multiplier of BASE_REWARD
        self.shaping = 0.1  # reward for a legal crossing of the net
        self.serve_court_aim = True
        self.log_game = log_game
        self.log_shots = False  # fill self.shot_log (one row per hit) for the evaluation's shot mix
        self.shot_log = []
        self.stats = dict(rallies=0, rally_shots=0, hits=0, games=0, faults=np.zeros(len(REASONS), int), points=np.zeros(2, int),
                          cords=0, mishits=0, q_sum=0.0)
        n = n_games
        self.pos = np.zeros((n, 2, 2))
        self.vel = np.zeros((n, 2, 2))
        self.shp = np.zeros((n, 3))
        self.shv = np.zeros((n, 3))
        self.score = np.zeros((n, 2), int)
        self.server = np.zeros(n, int)
        self.last_hitter = np.zeros(n, int)
        self.shots = np.zeros(n, int)
        self.since_hit = np.ones((n, 2))
        self.freeze = np.zeros((n, 2))
        self.react = np.zeros((n, 2))  # remaining reaction time
        self.disp = np.zeros(n)  # displacement d of each game's latest shot
        self.runlen = np.zeros((n, 2))  # cumulative path length (only while log_shots)
        self.land = np.zeros((n, 2))  # predicted landing (world x, z)
        self.land_t = np.zeros(n)
        self.rally_t = np.zeros(n)
        self.tsh = np.ones(n)  # time since the shuttle was last hit (drives the turnover drag)
        self._hit_fault = np.full(n, -1)  # fault reason set by a hit itself (net touch, high serve), -1 none
        self._act = np.zeros((n, 2, ACT_DIM))
        self._pend = np.full(n, -1)  # index in shot_log of each game's latest shot (its x_end is not known yet)
        self.t = 0.0
        self.nsub = 0  # sim steps (1/120 s) taken since construction / reset
        self.log = None
        self.dstep = 0  # decision steps taken
        self.rally_end = np.zeros(n, bool)  # per decision step: a rally ended in this game (winner -1 for a let)
        self.winner = np.full(n, -1)
        self.end_reason = np.zeros(n, int)
        self.end_shots = np.zeros(n, int)
        self.final_score = np.zeros((n, 2), int)  # score of the last finished game
        self.win_points = WIN_POINTS
        self.random_server_after_game = True  # training: random; match.py: the winner serves first in the next game
        self.builds = np.zeros((n, 2, 4), int)
        self.set_builds(slice(None), np.full((n, 2, 4), 6) if builds is None else builds)
        self.reset()

    def set_builds(self, idx, builds):
        """Builds [k, 2, 4] for the games idx (index array or slice); takes effect from the next step."""
        b = np.asarray(builds)
        if b.dtype.kind not in "iu" or b.shape[1:] != (2, 4) or b.min() < B.MIN or b.max() > B.MAX:
            raise ValueError("builds must be an integer array [k, 2, 4] with stats 1-10")
        self.builds[idx] = b
        self._p = {key: PT[key][self.builds[:, :, B.STATS.index(B.stat_of(key))]] for key in B.PARAM_KEYS}  # per-player [n, 2]
        self.power = self.builds[:, :, 0]
        norm = ((self.builds - 5.5) / 4.5).astype(np.float32)  # obs 29..36: own build, opponent build
        self._build_obs = np.concatenate([norm, norm[:, ::-1]], -1)

    # ------------------------------------------------------------ reset / rally start
    def reset(self):
        self.score[:] = 0
        self.server[:] = self.rng.integers(0, 2, self.n)
        if self.log_game is not None:
            self.log = dict(pos=[], vel=[], shuttle=[], events=[])
            self.t = 0.0
            self.nsub = 0
        self._start_rally(np.arange(self.n))
        if self.log_game is not None:
            self._log_step()
        return self._obs()

    def _start_rally(self, idx):
        if len(idx) == 0:
            return
        s = self.server[idx]
        r = 1 - s
        zs = service_z_sign(s, self.score[idx, s]) * SERVICE_Z
        self.pos[idx] = 0
        self.pos[idx, s, 0] = SIGN[s] * SERVER_X
        self.pos[idx, s, 1] = zs
        self.pos[idx, r, 0] = SIGN[r] * RECEIVER_X
        self.pos[idx, r, 1] = -zs
        self.vel[idx] = 0
        self.shp[idx, 0] = SIGN[s] * (SERVER_X - TOSS_AHEAD)
        self.shp[idx, 1] = TOSS_Y
        self.shp[idx, 2] = zs
        self.shv[idx] = 0
        self.last_hitter[idx] = r  # only the server may hit the toss
        self.shots[idx] = 0
        self.since_hit[idx] = 1.0
        self.freeze[idx] = 0
        self.react[idx] = 0
        self.disp[idx] = 0
        self.land[idx, 0], self.land[idx, 1] = self.shp[idx, 0], zs
        self.land_t[idx] = T_TOSS
        self.rally_t[idx] = 0
        self.tsh[idx] = 1.0
        self._pend[idx] = -1
        if self.log_game is not None and self.log_game in idx:
            g = self.log_game
            self._event("start", step=len(self.log["pos"]), s=int(self.server[g]), score=self.score[g].tolist(),
                        pos=self.pos[g].tolist(), shuttle=self.shp[g].tolist())

    # ------------------------------------------------------------ observation
    def _can_hit(self, p):
        """(normal reach, extended reach) of player p's racquet to the shuttle now, ignoring the swing."""
        sx = self.shp[:, 0] * SIGN[p]
        d = np.hypot(self.shp[:, 0] - self.pos[:, p, 0], self.shp[:, 2] - self.pos[:, p, 1])
        h = self.shp[:, 1]
        base = (self.last_hitter != p) & (sx > 0) & (self.since_hit[:, p] >= MIN_HIT_GAP) & (self.freeze[:, p] <= 0) & (h >= H_MIN)
        rs = self.reach_scale
        normal = base & (d <= self._p["reach"][:, p] * rs) & (h <= H_MAX)
        ext = base & (d <= self._p["reach_lunge"][:, p] * rs) & (h <= H_MAX_JUMP)
        return normal, ext

    def _obs(self):
        o = np.zeros((self.n, 2, OBS_DIM), np.float32)
        for p in (0, 1):
            m = MIRROR[p]
            q = 1 - p
            c = 0

            def put(*cols):
                nonlocal c
                for col in cols:
                    o[:, p, c] = col
                    c += 1

            put(self.pos[:, p, 0] * m / 7, self.pos[:, p, 1] * m / 3, self.vel[:, p, 0] * m / 5, self.vel[:, p, 1] * m / 5)
            put(self.pos[:, q, 0] * m / 7, self.pos[:, q, 1] * m / 3, self.vel[:, q, 0] * m / 5, self.vel[:, q, 1] * m / 5)
            put(self.shp[:, 0] * m / 7, self.shp[:, 1] / 5, self.shp[:, 2] * m / 3)
            put(np.clip(self.shv[:, 0] * m / 20, -4.5, 4.5), np.clip(self.shv[:, 1] / 20, -4.5, 4.5), np.clip(self.shv[:, 2] * m / 20, -4.5, 4.5))
            coming = (self.last_hitter == q) & (self.shots > 0)
            put(coming)
            put(self.land[:, 0] * m / 7, self.land[:, 1] * m / 3, np.clip(self.land_t, 0, 4) / 3)
            normal, ext = self._can_hit(p)
            put(normal, ext & ~normal)
            put(self.freeze[:, p] / FREEZE_JUMP, np.minimum(self.since_hit[:, p] / MIN_HIT_GAP, 1.0))
            sv = self.server == p
            put(sv & (self.shots == 0), sv)
            ss = self.score[np.arange(self.n), self.server]
            put(ss % 2, self.score[:, p] / CAP_POINTS, self.score[:, q] / CAP_POINTS, np.minimum(self.shots, 40) / 20)
            put(self.react[:, p] / max(REACTION_S, 1e-9))
            assert c == OBS_BASE
            o[:, p, OBS_BASE:] = self._build_obs[:, p]
        return o

    # ------------------------------------------------------------ step
    def step(self, actions):
        """actions [n, 2, 6] (clipped to [-1, 1]): target velocity (vx, vz) in the player's own view, swing (> 0 hits when
        reachable), aim x, aim z, flight time u. Returns obs [n, 2, O], reward [n, 2], done [n], info."""
        a = np.clip(actions, -1.0, 1.0)
        self._act = a
        self._tv = a[..., 0:2] * self._p["v_max"][..., None] * MIRROR[None, :, None]
        self._swing = a[..., 2] > 0
        reward = np.zeros((self.n, 2))
        done = np.zeros(self.n, bool)
        self.rally_end[:] = False
        self.dstep += 1
        for _ in range(DECISION):
            self._substep(reward, done)
        if BASE_REWARD and self.base_scale:
            wait = self.shots > 0
            for p in (0, 1):
                d2 = (self.pos[:, p, 0] - SIGN[p] * BASE_X) ** 2 + self.pos[:, p, 1] ** 2
                reward[:, p] += self.base_scale * BASE_REWARD * np.exp(-d2 / (2 * BASE_SIGMA**2)) * (wait & (self.last_hitter == p))
        info = dict(stats=self.stats, rally_end=self.rally_end, winner=self.winner, reason=self.end_reason,
                    shots=self.end_shots, final_score=self.final_score)
        return self._obs(), reward, done, info

    def _substep(self, reward, done):
        n = self.n
        dt = DT
        # --- players
        tv = np.where((self.react > 0)[..., None], self.vel, self._tv)  # reacting: hold the current velocity
        tv = np.where((self.freeze > 0)[..., None], 0.0, tv)
        tv = np.where((self.shots == 0)[:, None, None], 0.0, tv)  # Law 9.1: both stand still until the serve is struck
        nrm = np.linalg.norm(tv, axis=-1, keepdims=True)
        tv = tv * np.minimum(1.0, self._p["v_max"][..., None] / np.maximum(nrm, 1e-9))
        dv = tv - self.vel
        dn = np.linalg.norm(dv, axis=-1, keepdims=True)
        self.vel += dv * np.minimum(1.0, self._p["a_max"][..., None] * dt / np.maximum(dn, 1e-9))
        self.pos += self.vel * dt
        xs = self.pos[..., 0] * SIGN
        xc = np.clip(xs, X_MIN_P, X_MAX_P)
        hit_x = xc != xs
        self.pos[..., 0] = xc * SIGN
        self.vel[..., 0] = np.where(hit_x, 0.0, self.vel[..., 0])
        zc = np.clip(self.pos[..., 1], -Z_MAX_P, Z_MAX_P)
        self.vel[..., 1] = np.where(zc != self.pos[..., 1], 0.0, self.vel[..., 1])
        self.pos[..., 1] = zc
        np.maximum(self.freeze - dt, 0.0, out=self.freeze)
        np.maximum(self.react - dt, 0.0, out=self.react)
        if self.log_shots:
            self.runlen += np.linalg.norm(self.vel, axis=-1) * dt
        self.since_hit += dt
        self.rally_t += dt
        self.land_t -= dt
        # --- shuttle
        old = self.shp.copy()
        self.shp, self.shv = rk2(self.shp, self.shv, self.tsh)
        self.tsh += dt
        self.t += dt
        self.nsub += 1
        self._net_cord(old)
        hitter = self.last_hitter
        cross = old[:, 0] * self.shp[:, 0] < 0
        with np.errstate(all="ignore"):
            f = old[:, 0] / (old[:, 0] - self.shp[:, 0])
            yc = old[:, 1] + f * (self.shp[:, 1] - old[:, 1])
            zc = old[:, 2] + f * (self.shp[:, 2] - old[:, 2])
        net_fault = cross & (yc < tape_height(zc)) & (self.shots > 0)
        legal = cross & ~net_fault & (self.shots > 0)
        if self.shaping:
            reward[legal, hitter[legal]] += self.shaping
        counted = legal & (self.shots <= SHOT_REWARD_MAX_SHOTS)
        if SHOT_REWARD:
            reward[counted, hitter[counted]] += SHOT_REWARD
        if DEPTH_REWARD:
            lx_, lz_ = self.land[:, 0], self.land[:, 1]
            depth = np.clip((np.abs(lx_) - SHORT_X) / (HL - SHORT_X), 0.0, 1.0) * in_bounds(lx_, lz_)
            reward[counted, hitter[counted]] += DEPTH_REWARD * depth[counted]
        if DISP_REWARD:
            reward[counted, hitter[counted]] += DISP_REWARD * np.clip(self.disp[counted] / DISP_NORM, 0.0, 1.0)
        landed = self.shp[:, 1] <= 0
        with np.errstate(all="ignore"):
            fl = old[:, 1] / (old[:, 1] - self.shp[:, 1])
        lx = old[:, 0] + fl * (self.shp[:, 0] - old[:, 0])
        lz = old[:, 2] + fl * (self.shp[:, 2] - old[:, 2])
        oob = (self.shots > 0) & ~landed & ~net_fault & ((self.shp[:, 1] > CEILING_Y) | (np.abs(self.shp[:, 0]) > HALL_X)
                                                         | (np.abs(self.shp[:, 2]) > HALL_Z))  # ceiling / walls: the hitter's fault
        ended = net_fault | landed | oob
        # who loses and why
        loser = np.where(self.shots == 0, self.server, hitter)
        own_side = lx * SIGN[hitter] > 0
        serve_shot = self.shots == 1
        zsgn = -service_z_sign(self.server, self.score[np.arange(n), self.server])  # diagonal court
        good = np.where(serve_shot, in_service_court(lx, lz, zsgn), in_bounds(lx, lz))
        landing_reason = np.where(self.shots == 0, SERVE, np.where(own_side, NET, np.where(good, MISSED, np.where(serve_shot, SERVE, OUT))))
        reason = np.where(net_fault, np.where(self.shots == 0, SERVE, NET), np.where(oob, CEILING, landing_reason))
        hitter_wins = reason == MISSED
        loser = np.where(hitter_wins, 1 - hitter, loser)
        if self.log_game is not None:
            self._log_step()
        # --- hits (games whose rally continues)
        alive = ~ended
        self._hit_fault[:] = -1
        for p in (0, 1):
            _, ext = self._can_hit(p)
            idx = np.nonzero(alive & self._swing[:, p] & ext)[0]
            if len(idx):
                self._hit(idx, p)
        hf = self._hit_fault >= 0  # the hit itself was a fault (net touch, serve struck too high): the hitter loses at once
        if hf.any():
            reason = np.where(hf, self._hit_fault, reason)
            loser = np.where(hf, self.last_hitter, loser)
            lx = np.where(hf, self.shp[:, 0], lx)
            lz = np.where(hf, self.shp[:, 2], lz)
        lx, lz = np.where(oob, self.shp[:, 0], lx), np.where(oob, self.shp[:, 2], lz)
        capped = alive & (self.shots >= RALLY_CAP_SHOTS)  # a rally this long is replayed
        end_idx = np.nonzero(ended | capped | hf)[0]
        if len(end_idx):
            self._finish(end_idx, loser, reason, capped, landed, lx, lz, reward, done)

    def _net_cord(self, old):
        """Net-cord contact for the step old -> self.shp: where the segment first enters the circle of radius CORD_R around the tape axis
        (in the x-y plane), the normal velocity component is reflected with CORD_E, the tangential part (also z) damped by CORD_TANGENT,
        x gets CORD_SIGMA noise, and the shuttle sits on the circle. The flight then continues under the drag model."""
        new = self.shp
        ht = tape_height(0.5 * (old[:, 2] + new[:, 2]))
        fx, fy = old[:, 0], old[:, 1] - ht
        dx, dy = new[:, 0] - fx, new[:, 1] - old[:, 1]
        a = dx * dx + dy * dy
        b = 2 * (fx * dx + fy * dy)
        c = fx * fx + fy * fy - CORD_R**2
        with np.errstate(all="ignore"):
            disc = b * b - 4 * a * c
            t1 = (-b - np.sqrt(disc)) / (2 * a)
        idx = np.nonzero((a > 0) & (c > 0) & (disc > 0) & (t1 >= 0) & (t1 <= 1))[0]
        if len(idx) == 0:
            return
        t1 = t1[idx]
        nx, ny = (fx[idx] + t1 * dx[idx]) / CORD_R, (fy[idx] + t1 * dy[idx]) / CORD_R  # normal at the contact point
        v = self.shv[idx]
        vn = v[:, 0] * nx + v[:, 1] * ny
        m = vn < 0  # moving into the tape
        if not m.any():
            return
        idx, t1, nx, ny, vn, v = idx[m], t1[m], nx[m], ny[m], vn[m], v[m]
        tx_, ty_ = v[:, 0] - vn * nx, v[:, 1] - vn * ny
        vx = -CORD_E * vn * nx + CORD_TANGENT * tx_ + self.rng.normal(size=len(idx)) * CORD_SIGMA
        vy = -CORD_E * vn * ny + CORD_TANGENT * ty_
        pc = old[idx] + t1[:, None] * (new[idx] - old[idx])
        self.shp[idx, 0] = pc[:, 0] + nx * 1e-4
        self.shp[idx, 1] = pc[:, 1] + ny * 1e-4
        self.shp[idx, 2] = pc[:, 2]
        self.shv[idx, 0], self.shv[idx, 1], self.shv[idx, 2] = vx, vy, CORD_TANGENT * v[:, 2]
        self.stats["cords"] += len(idx)
        if self.log_game is not None and self.log_game in idx:
            g = self.log_game
            self._event("cord", pos=self.shp[g].tolist(), v=self.shv[g].tolist(), tau=float(self.tsh[g]))

    def _hit(self, idx, p):
        """Executes the shot of player p in games idx (action = self._act[idx, p])."""
        a = self._act[idx, p]
        m = MIRROR[p]
        k = len(idx)
        sh = self.shp[idx]
        h0 = sh[:, 1]
        jump = h0 > H_MAX
        lunge = np.hypot(sh[:, 0] - self.pos[idx, p, 0], sh[:, 2] - self.pos[idx, p, 1]) > self._p["reach"][idx, p] * self.reach_scale
        extended = lunge | jump
        first = self.shots[idx] == 0
        serve = first
        tx = m * (HL / 2 + HL / 2 * a[:, 3])
        tz = m * (HW * a[:, 4])
        # A serve's aim is normalised over the diagonal service court instead of the whole half, so a policy cannot get stuck
        # always serving into the wrong court: x short line .. back line, z centre line .. side line on the diagonal side.
        sv = self.server[idx]
        zd = -service_z_sign(sv, self.score[idx, sv])
        first = first & self.serve_court_aim
        tx = np.where(first, m * (SHORT_X + (HL - SHORT_X) * (a[:, 3] + 1) / 2), tx)
        tz = np.where(first, zd * HW * (a[:, 4] + 1) / 2, tz)
        u = a[:, 5]
        D, dx, dz = self._dir(tx - sh[:, 0], tz - sh[:, 2], p)
        tab = self.table
        pw = self.power[idx, p]
        tmin = tab.t_min(D, h0, pw)
        tmax = np.clip(np.maximum(1.0 + 0.1 * D, tmin + 0.25), 0.3, T_GRID[-1])
        T = tmin + (u + 1) / 2 * np.maximum(tmax - tmin, 0.0)
        vh, vy, _ = tab.lookup(D, h0, T)
        speed0 = np.hypot(vh, vy)
        pspeed = np.linalg.norm(self.vel[idx, p], axis=-1)
        # contact point in the hitter's body frame: forward (toward the net), lateral (racquet side +; the right of a player facing the net
        # is +z for player 0 and -z for player 1), height
        cf = -SIGN[p] * (sh[:, 0] - self.pos[idx, p, 0])
        cl = m * (sh[:, 2] - self.pos[idx, p, 1])
        q = contact_quality(cf, cl, h0, self._p["reach"][idx, p] * self.reach_scale, extended)
        sigma = ((0.15 + 0.25 * speed0 / 90 + SMASH_NOISE * (speed0 / 90) ** 2 + 0.3 * extended + 0.2 * pspeed / self._p["v_max"][idx, p])
                 * self._p["noise_mult"][idx, p] * self.noise_scale * (1 + Q_NOISE * (1 - q)))
        tx2 = tx + self.rng.normal(size=k) * sigma
        tz2 = tz + self.rng.normal(size=k) * sigma
        D2, dx2, dz2 = self._dir(tx2 - sh[:, 0], tz2 - sh[:, 2], p)
        vh, vy, apex = tab.lookup(D2, h0, T)
        sp2 = np.hypot(vh, vy)
        scale = np.minimum(1.0, launch_cap(h0, pw) * (Q_SPEED[0] + Q_SPEED[1] * q) / np.maximum(sp2, 1e-9))
        vh, vy = vh * scale, vy * scale
        mis = q < MISHIT_Q  # a frame hit: slow and in a random direction
        if mis.any():
            km = int(mis.sum())
            e = self.rng.normal(size=(km, 2)) * MISHIT_SIGMA
            sm_ = MISHIT_SPEED * np.hypot(vh[mis], vy[mis])
            el = np.arctan2(vy[mis], vh[mis]) + e[:, 0]
            hd = np.arctan2(dz2[mis], dx2[mis]) + e[:, 1]
            vh[mis], vy[mis] = sm_ * np.cos(el), sm_ * np.sin(el)
            dx2, dz2 = dx2.copy(), dz2.copy()
            dx2[mis], dz2[mis] = np.cos(hd), np.sin(hd)
        self.shv[idx, 0], self.shv[idx, 1], self.shv[idx, 2] = vh * dx2, vy, vh * dz2
        self.land[idx, 0], self.land[idx, 1] = tx2, tz2
        self.land_t[idx] = T
        self.disp[idx] = np.hypot(tx2 - self.pos[idx, 1 - p, 0], tz2 - self.pos[idx, 1 - p, 1])
        self.react[idx, 1 - p] = REACTION_S
        self.react[idx, p] = 0.0
        self.last_hitter[idx] = p
        self.since_hit[idx, p] = 0.0
        self.tsh[idx] = 0.0
        self._hit_fault[idx] = np.where((np.abs(sh[:, 0]) < NET_TOUCH_X) & extended, NET_TOUCH, np.where(serve & (h0 > SERVE_MAX_H), SERVE, -1))
        self.stats["mishits"] += int(mis.sum())
        self.stats["q_sum"] += float(q.sum())
        self.freeze[idx, p] = np.where(jump, FREEZE_JUMP, np.where(lunge, FREEZE_LUNGE, 0.0))
        self.shots[idx] += 1
        self.stats["hits"] += k
        if self.log_shots:
            self._log_shots(idx, p, first, h0, sh[:, 0], vh, vy, apex, tx2, tz2, extended, q)
        if self.log_game is not None and self.log_game in idx:
            j = int(np.nonzero(idx == self.log_game)[0][0])
            self._event("hit", p=p, h=float(h0[j]), lunge=bool(lunge[j]), jump=bool(jump[j]), pos=sh[j].tolist(),
                        v=self.shv[idx[j]].tolist(), target=[float(tx2[j]), float(tz2[j])], T=float(T[j]), q=float(q[j]),
                        body=[float(cf[j]), float(cl[j]), float(h0[j])], mishit=bool(mis[j]))

    @staticmethod
    def _dir(dx, dz, p):
        D = np.hypot(dx, dz)
        ok = D > 1e-6
        ux = np.where(ok, dx / np.maximum(D, 1e-9), -SIGN[p])
        uz = np.where(ok, dz / np.maximum(D, 1e-9), 0.0)
        return D, ux, uz

    def _log_shots(self, idx, p, first, h0, x0, vh, vy, apex, tx, tz, ext, q):
        for j, g in enumerate(idx):
            if self._pend[g] >= 0:  # the previous shot of this game ends at this contact
                self.shot_log[self._pend[g]][8] = abs(x0[j])
                self._close_run(g, p)
            self._pend[g] = len(self.shot_log)
            # game, player, first, h, |x| contact, v0, launch angle deg, apex, |x| end (intended landing until updated), decision step
            self.shot_log.append([int(g), p, bool(first[j]), float(h0[j]), abs(float(x0[j])), float(np.hypot(vh[j], vy[j])),
                                  float(np.degrees(np.arctan2(vy[j], vh[j]))), float(apex[j]), abs(float(tx[j])), self.dstep,
                                  float(self.disp[g]), 0.0, float(self.runlen[g, 1 - p]), float(q[j])])  # + d, opponent run (filled later), its start, contact quality

    def _close_run(self, g, opp):
        """Row [11] of game g's latest shot = path length its receiver (player opp) ran since that hit."""
        row = self.shot_log[self._pend[g]]
        row[11] = float(self.runlen[g, opp]) - row[12]

    # ------------------------------------------------------------ rally end
    def _finish(self, idx, loser, reason, capped, landed, lx, lz, reward, done):
        st = self.stats
        game_over = []
        for g in idx:
            let = bool(capped[g] or (self.shots[g] >= RALLY_CAP_SHOTS))
            shots = int(self.shots[g])
            r = LET if let else int(reason[g])
            st["rallies"] += 1
            st["rally_shots"] += shots
            st["faults"][r] += 1
            self.rally_end[g], self.winner[g], self.end_reason[g], self.end_shots[g] = True, -1, r, shots
            if self.log_shots and self._pend[g] >= 0 and not let:
                self.shot_log[self._pend[g]][8] = abs(float(lx[g])) if landed[g] else 0.0
                self._close_run(g, 1 - self.last_hitter[g])
            if self.log_game == g:
                self._event("land", x=float(lx[g]), z=float(lz[g]), reason=REASONS[r], shots=shots, pos=self.pos[g].tolist(), vel=self.vel[g].tolist())
            if let:
                continue
            lo = int(loser[g])
            win = 1 - lo
            if r == SERVE:
                reward[g, lo] -= SERVE_FAULT_PENALTY
            reward[g, win] += 1.0
            reward[g, lo] -= 1.0
            self.score[g, win] += 1
            st["points"][win] += 1
            self.server[g] = win
            self.winner[g] = win
            if self.log_game == g:
                self._event("point", winner=win, reason=REASONS[r], score=self.score[g].tolist(), shots=shots)
            if is_over(self.score[g, win], self.score[g, lo], self.win_points):
                game_over.append(g)
        for g in game_over:
            done[g] = True
            st["games"] += 1
            self.final_score[g] = self.score[g]
            self.score[g] = 0
            if self.random_server_after_game:
                self.server[g] = self.rng.integers(0, 2)  # else the winner of the game serves first in the next one
            if self.log_game == g:
                self._event("game_over")
        self._start_rally(idx)

    def set_first_server(self, server):
        """Games with log_game: restart the first rally with this server (0 / 1) instead of the seeded random one."""
        self.server[:] = server
        self.log = dict(pos=[], vel=[], shuttle=[], events=[])
        self.t, self.nsub = 0.0, 0
        self._start_rally(np.arange(self.n))
        self._log_step()

    # ------------------------------------------------------------ logging for export
    def _event(self, kind, step=None, **kw):
        step = len(self.log["pos"]) - 1 if step is None else step
        self.log["events"].append(dict(kind=kind, t=self.t, k=self.nsub, step=step, **kw))

    def _log_step(self):
        g = self.log_game
        self.log["pos"].append(self.pos[g].copy())
        self.log["vel"].append(self.vel[g].copy())
        self.log["shuttle"].append(np.concatenate([self.shp[g], self.shv[g]]))

    def serialize_game(self, i):
        """Per-sim-step (120 Hz) logs of game i (needs BadSim(..., log_game=i)): pos [S,2,2], vel [S,2,2], shuttle [S,6]
        (x,y,z,vx,vy,vz) and the event list (kinds: start, hit, land, point, game_over; `step` indexes the arrays, `k` is the sim step
        count at the event, `t` the sim time)."""
        assert i == self.log_game, "construct BadSim(..., log_game=i) to serialize game i"
        return dict(pos=np.array(self.log["pos"]), vel=np.array(self.log["vel"]), shuttle=np.array(self.log["shuttle"]), events=list(self.log["events"]), dt=DT)
