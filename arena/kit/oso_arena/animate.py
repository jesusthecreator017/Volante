"""Match log -> replay.bin the viewers play (what tools/badsim/play_match.py wrote before the split, minus the `oso-imu` swings pass).

Layout: lead-in 1 s, then every rally (60 fps frames taken from the 120 Hz sim steps), then a 4 s gap between rallies in which the
players walk to the next service positions (the shuttle is hidden), so rallies are always more than the stats' 3 s rally gap apart.
The 120 Hz sim steps are rebuilt from the 30 Hz log: players by cubic Hermite through the logged positions and velocities (anchored at
the rally start and landing), the shuttle by re-integrating each flight from its hit (and net-cord) event with the sim's own RK2 integrator, turnover drag included.
Bodies: body.player_pose along the players' root paths, swings timed so the forward-swing peak is the contact (pitch tilted to the
contact height), facing the shuttle around a contact and the net otherwise, lunge = hip drop, jump = hip rise. Racquet contact
(phase 14.2b): the body is moved (root path + a lift / crouch) so the contact is within arm's reach and the right arm is solved by IK so
that the racquet's sweet spot (as the viewer draws it: along the forearm from the wrist) is exactly on the shuttle at every contact.
Tracks: joints3d [F,2,17,4], shuttle [F,8] (flight_id per shot, conf 1 in flight), events (1 contact, 2 landing, 3 point)."""
import json
import struct
from pathlib import Path

import numpy as np

from oso_arena import body as sm
from oso_arena import sim

FPS = 60.0
LEAD_S, TAIL_S = 1.0, 1.5
DECEL_S, WALK_S, REST_S = 0.4, 2.2, 1.4  # between rallies (sum 4.0 s)
SWEET_FROM_WRIST = 0.55  # racquet 0.68 m, sweet spot 0.55 m from the grip; the viewer puts the grip at the wrist, along the forearm
ARM_MAX = sm.UPPER_ARM + sm.FOREARM - 2e-3  # longest shoulder -> wrist distance (the body's own full arm)
REACH_MAX = 1.08  # shoulder -> sweet spot at most this far (arm 0.57 m + 0.55 m is the straight-arm limit 1.12 m)
REACH_MIN = 0.78  # ... and at least this far: with the racquet pointing out along the forearm a closer contact needs a folded-back arm (the
#                  IK finds no pose below ~0.75 m), so a cramped contact steps the body away from the shuttle
BUMP_S = 0.35  # a body shift for a contact rises over this long, falls over the same
PIN_HALF_S, PIN_BEFORE_S, PIN_AFTER_S = 0.05, 0.15, 0.2  # the sweet spot is exactly on the contact for +-PIN_HALF_S, blended in / out around
PAUSE_CAP_S = 8.0  # an interval (60 / 120 s in the match) is shown as at most this long pause; the log keeps the real time
SHOULDER_Y, REACH_FROM_SHOULDER = 1.4, 1.1  # standing shoulder height; shoulder -> racquet head centre at full stretch
MAGIC = b"OSOR"


# ---------------------------------------------------------------- replay.bin writer (format: docs/data-format.md; tools/replay.py `write`)
def _pad(n):
    return -n % 16


def write_replay(path, fps, tracks):
    """tracks: list of (meta dict, array); arrays are written little-endian f32. Same bytes as tools/replay.py write()."""
    blob, headers = b"", []
    for meta, arr in tracks:
        arr = np.ascontiguousarray(arr, "<f4")
        headers.append({**meta, "dtype": "f32", "shape": list(arr.shape), "offset": len(blob)})
        blob += arr.tobytes()
        blob += b"\0" * _pad(len(blob))
    frame_count = next(h for h in headers if h.get("per_frame", True))["shape"][0]
    head = json.dumps({"version": 1, "fps": fps, "frame_count": frame_count, "start_time": 0.0,
                       "coordinate_system": "court_m_yup_xlength", "tracks": headers}).encode()
    head += b"\0" * _pad(8 + len(head))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(MAGIC + struct.pack("<I", len(head)) + head + blob)


# ---------------------------------------------------------------- log -> 120 Hz rally arrays
def _rally_spans(events):
    """[(start event, hits, land event, point event)] for finished rallies with a point (lets are skipped)."""
    out, cur = [], None
    for e in events:
        if e["kind"] == "start":
            cur = dict(start=e, hits=[], cords=[], land=None, point=None)
        elif cur is None:
            continue
        elif e["kind"] == "hit":
            cur["hits"].append(e)
        elif e["kind"] == "cord":
            cur["cords"].append(e)
        elif e["kind"] == "land":
            cur["land"] = e
        elif e["kind"] == "point":
            cur["point"] = e
            out.append(cur)
    return out


def _hermite(tk, pk, vk, m):
    """Cubic Hermite through knots (sim steps tk, positions pk [K,..], velocities vk [K,..] in m/s) sampled at steps m."""
    i = np.clip(np.searchsorted(tk, m, "right") - 1, 0, len(tk) - 2)
    h = (tk[i + 1] - tk[i]) * sim.DT
    u = ((m - tk[i]) * sim.DT / h)[:, None, None]
    h = h[:, None, None]
    u2, u3 = u * u, u * u * u
    return ((2 * u3 - 3 * u2 + 1) * pk[i] + (u3 - 2 * u2 + u) * h * vk[i] + (-2 * u3 + 3 * u2) * pk[i + 1] + (u3 - u2) * h * vk[i + 1])


def expand(log, spans):
    """pos [K,2,2] and shuttle [K,6] at 120 Hz, valid inside the given rallies (zeros elsewhere)."""
    K = max(s["land"]["k"] for s in spans) + 1
    pos, shuttle = np.zeros((K, 2, 2)), np.zeros((K, 6))
    fpos, fvel = log.frames["pos"].astype(float), log.frames["vel"].astype(float)
    flights = []  # (start step, end step, p0, v0) in chronological order
    for r in spans:
        k0, kL = r["start"]["k"], r["land"]["k"]
        f = np.arange(k0 // 4 + 1, kL // 4 + 1)  # frames strictly after the start, up to the landing step
        tk, pk, vk = [k0, *(f * 4)], [np.array(r["start"]["pos"]), *fpos[f]], [np.zeros((2, 2)), *fvel[f]]
        if tk[-1] != kL:
            tk.append(kL)
            pk.append(np.array(r["land"]["pos"]))
            vk.append(np.array(r["land"]["vel"]))
        tk, pk, vk = np.array(tk, int), np.array(pk), np.array(vk)
        pos[k0:kL + 1] = _hermite(tk, pk, vk, np.arange(k0, kL + 1))
        # a flight starts at the toss, at each hit (turnover drag from tau 0) and at each net-cord contact (tau as logged); a cord goes
        # before a hit of the same step (the sim does the cord first)
        starts = [(k0, np.array(r["start"]["shuttle"]), np.zeros(3), 1.0)]
        starts += sorted([(e["k"], np.array(e["pos"]), np.array(e["v"]), e["tau"]) for e in r["cords"]]
                         + [(h["k"], np.array(h["pos"]), np.array(h["v"]), 0.0) for h in r["hits"]], key=lambda x: x[0])
        for i, (k, p, v, tau) in enumerate(starts):
            flights.append((k, starts[i + 1][0] if i + 1 < len(starts) else kL, p, v, tau))
    # integrate all flights together, one RK2 step per round
    p = np.array([f[2] for f in flights])
    v = np.array([f[3] for f in flights])
    tau = np.array([f[4] for f in flights])
    n = np.array([f[1] - f[0] for f in flights])
    rows = np.empty((n.max() + 1, len(flights), 6))
    for j in range(n.max() + 1):
        rows[j] = np.concatenate([p, v], -1)
        p, v = sim.rk2(p, v, tau)
        tau = tau + sim.DT
    for i, (k, end, *_) in enumerate(flights):  # chronological: a flight's last row is replaced by the next flight's first
        shuttle[k:end + 1] = rows[:end - k + 1, i]
    return pos, shuttle


def build(log):
    """Concatenate lead-in, rallies and gaps into per-frame arrays + events. Returns a dict."""
    spans = _rally_spans(log.events)
    if not spans:
        raise ValueError("the match log has no finished rally to animate")
    gpos, gsh = expand(log, spans)
    pos, shuttle, events, ev_hits = [], [], [], [[], []]
    flight = 0
    sheet = []

    def flipped(r):  # A stands on +X in this rally: rotate the court 180 degrees and let the replay slots follow the world sides
        return bool(r["start"].get("ends", 0))

    def pos_at(r, a, b=None):
        p = gpos[a:b] if b is not None else gpos[a]
        return (-p[..., ::-1, :]) if flipped(r) else p

    start_pos = pos_at(spans[0], spans[0]["start"]["k"])
    pos += [start_pos] * int(LEAD_S * FPS)
    shuttle += [np.zeros(8)] * int(LEAD_S * FPS)
    for ri, r in enumerate(spans):
        fl = flipped(r)
        s0, L = r["start"]["k"], r["land"]["k"]
        F0 = len(pos)
        steps = np.arange(s0, L, 2)
        slot = (lambda p_: 1 - p_) if fl else (lambda p_: p_)
        hit_steps = np.array([h["k"] for h in r["hits"]], int)
        # shuttle rows: a hit makes its own step's row the first of a new flight, with the post-hit velocity
        fid = flight + np.array([(hit_steps <= st).sum() for st in steps])
        gs = gsh[s0:L + 1].copy()
        if fl:
            gs[:, [0, 2, 3, 5]] *= -1
        sh = np.zeros((len(steps), 8))
        sh[:, :6] = gs[steps - s0]
        sh[:, 6], sh[:, 7] = 1.0, fid
        p = pos_at(r, s0, L + 1)[steps - s0]
        t_of = lambda step, F0=F0, s0=s0: (F0 + (step - s0) / 2) / FPS  # noqa: E731
        sgn = -1.0 if fl else 1.0
        lx, lz = sgn * r["land"]["x"], sgn * r["land"]["z"]
        land_t = t_of(L)
        if r["point"]["reason"] == "net" and sh[-1, 1] > 0.12:  # into the net: the shuttle drops from where it crossed the tape
            a, b = gs[L - 1 - s0, :3], gs[L - s0, :3]
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
        # a hit at the very end of the rally (a fault at the contact): the shuttle flies on for a few frames so the contact frame exists
        last = r["hits"][-1] if r["hits"] else None
        if last is not None and (last["k"] - s0 + 1) // 2 + 2 > len(sh):
            n_more = (last["k"] - s0 + 1) // 2 + 3 - len(sh)
            pk, vk = np.array(last["pos"]) * [sgn, 1, sgn], np.array(last["v"]) * [sgn, 1, sgn]
            more, done = [], 0
            for j in range(len(sh), len(sh) + n_more):
                while done < max(0, s0 + 2 * j - last["k"]):
                    pk, vk = sim.rk2(pk, vk, done * sim.DT)
                    done += 1
                more.append(np.concatenate([pk, vk, [1.0, float(flight_after) + 1]]))
            sh = np.vstack([sh, more])
            p = np.vstack([p, np.repeat(p[-1:], n_more, 0)])
            flight_after += 1
        for h in r["hits"]:  # the shuttle is exactly at the contact point in the contact frame
            j = frame_of(t_of(h["k"])) - F0
            if 0 <= j < len(sh):
                sh[j, :3] = np.array(h["pos"]) * [sgn, 1.0, sgn]
        shuttle += list(sh)
        pos += list(p)
        for h in r["hits"]:
            t = t_of(h["k"])
            at = np.array(h["pos"]) * [sgn, 1.0, sgn]
            events.append([t, 1, slot(h["p"]), *at])
            ev_hits[slot(h["p"])].append(dict(t=t, h=h["h"], lunge=h["lunge"], jump=h["jump"], at=at, q=h["q"]))
        events.append([land_t, 2, -1, lx, 0.0, lz])
        w = r["point"]["winner"]
        sc = r["point"]["score"]
        events.append([land_t, 3, slot(w), sc[w], sc[1 - w], 0.0])
        sheet.append(dict(rally=ri + 1, start_t=t_of(s0), end_t=land_t, server=int(slot(r["start"]["s"])), shots=len(r["hits"]),
                          winner=int(slot(w)), reason=r["point"]["reason"], score=[int(sc[0]), int(sc[1])]))
        flight = flight_after + 1
        # gap to the next rally (or the tail); an interval before the next rally adds a pause
        if ri + 1 < len(spans):
            nx = spans[ri + 1]
            nxt = pos_at(nx, nx["start"]["k"])
            gap = gap_frames(np.array(pos[-1]), np.array(pos[-1]) - np.array(pos[-2]), nxt)
            gap += [nxt] * int(min(nx["start"].get("interval_before_s", 0.0), PAUSE_CAP_S) * FPS)
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
        out.append(mid + (p_next - mid) * sm.mj(i / nw))
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


def frame_of(t):
    """The frame of time t (a contact exactly between two frames goes to the later one)."""
    return int(np.floor(t * FPS + 0.5 + 1e-6))


def sweet_spot(J):
    """Racquet sweet spot [..., 3] of joints J [..., 17, 3] the way the viewer draws the racquet without an IMU (scene/racquet.rs
    estimate_from_arm: grip at the right wrist, along the forearm elbow -> wrist)."""
    fore = J[..., 10, :] - J[..., 8, :]
    return J[..., 10, :] + SWEET_FROM_WRIST * fore / np.linalg.norm(fore, axis=-1, keepdims=True)


def _right_hint(fwd):
    """The elbow hint body.player_pose uses for the right arm."""
    up = np.array([0, 1.0, 0])
    return -up - 0.3 * fwd + 0.5 * -np.cross(up, fwd)


def _elbow(S, W, hint):
    h = hint - (hint @ (W - S)) * (W - S) / max((W - S) @ (W - S), 1e-12)
    if np.linalg.norm(h) < 0.15:  # the arm points along the hint: bend sideways instead
        hint = np.cross(W - S, [0, 1.0, 0]) if abs((W - S)[1]) < 0.9 * np.linalg.norm(W - S) else np.array([1.0, 0, 0])
    return sm.ik(S, W, sm.UPPER_ARM, sm.FOREARM, hint)


_DIRS = None


def _sphere(n=1200):
    """Fibonacci points on the unit sphere."""
    k = np.arange(n) + 0.5
    phi, th = np.arccos(1 - 2 * k / n), np.pi * (1 + 5**0.5) * k
    return np.stack([np.cos(th) * np.sin(phi), np.cos(phi), np.sin(th) * np.sin(phi)], -1)


def _elbow_batch(S, W, hint):
    """sm.ik for many wrists W [N,3] from one shoulder (the elbow bends toward `hint`)."""
    v = W - S
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    u = v / np.maximum(n, 1e-9)
    h = hint - (u @ hint)[:, None] * u
    h /= np.maximum(np.linalg.norm(h, axis=-1, keepdims=True), 1e-9)
    d = np.minimum(n, sm.UPPER_ARM + sm.FOREARM - 1e-4)
    x = (sm.UPPER_ARM**2 - sm.FOREARM**2 + d**2) / (2 * np.maximum(d, 1e-9))
    return S + x * u + np.sqrt(np.maximum(sm.UPPER_ARM**2 - x**2, 0.0)) * h


def solve_arm(S, P, hint, W0=None, iters=14):
    """Wrist W and elbow E of a right arm from shoulder S so that the sweet spot W + SWEET_FROM_WRIST * unit(W - E) is at P. The arm
    pose is searched over the forearm direction (a sphere grid, wrists closer than 0.3 m to the shoulder - arm folded back - are
    penalised), then refined by Newton on W (numerical Jacobian) from the best guess or from `W0` if that is already close. If P is
    out of reach the arm ends as close as it gets. Returns (W, E, miss in m)."""
    global _DIRS
    if _DIRS is None:
        _DIRS = _sphere()

    def res(W):
        v = W - S
        n = np.linalg.norm(v)
        if n > ARM_MAX:  # a real arm: the wrist stays within the arm's length
            W = S + v * (ARM_MAX / n)
        E = _elbow(S, W, hint)
        return W + SWEET_FROM_WRIST * (W - E) / np.linalg.norm(W - E) - P, E

    def newton(W):
        r, E = res(W)
        for _ in range(iters):
            if np.linalg.norm(r) < 1e-4:
                break
            Jm = np.empty((3, 3))
            for a in range(3):
                d = np.zeros(3)
                d[a] = 1e-4
                Jm[:, a] = (res(W + d)[0] - r) / 1e-4
            try:
                dw = -np.linalg.solve(Jm, r)
            except np.linalg.LinAlgError:
                break
            dw = dw * min(1.0, 0.25 / max(np.linalg.norm(dw), 1e-12))
            for damp in (1.0, 0.5, 0.25, 0.1):  # backtrack until the miss shrinks
                r2, E2 = res(W + damp * dw)
                if np.linalg.norm(r2) < np.linalg.norm(r):
                    W, r, E = W + damp * dw, r2, E2
                    break
            else:
                break
        v = W - S
        if np.linalg.norm(v) > ARM_MAX:
            W = S + v * (ARM_MAX / np.linalg.norm(v))
            E = _elbow(S, W, hint)
        return W, E, float(np.linalg.norm(r))

    best = None
    if W0 is not None:
        best = newton(np.array(W0, float))
        if best[2] > 1e-3 or np.linalg.norm(best[0] - S) < 0.25:
            best = None
    if best is None:
        Ws = P - SWEET_FROM_WRIST * _DIRS
        Es = _elbow_batch(S, Ws, hint)
        d_act = Ws - Es
        d_act /= np.linalg.norm(d_act, axis=-1, keepdims=True)
        wr = np.linalg.norm(Ws - S, axis=-1)
        score = np.linalg.norm(Ws + SWEET_FROM_WRIST * d_act - P, axis=-1) + 0.5 * np.maximum(0.3 - wr, 0.0) + 5.0 * np.maximum(wr - 0.567, 0.0)
        best = None
        for g in np.argsort(score)[:4]:  # refine the few best guesses
            cand = newton(Ws[g])
            if best is None or cand[2] < best[2]:
                best = cand
            if best[2] < 1e-3:
                break
    return best


def pick_hint(S, P, hint):
    """Elbow hint for a pinned contact: `hint` (the body's own) if it can put the sweet spot on P with an arm that is not folded back,
    else the best of the 26 sign combinations of the three body axes (an arm pointing along the hint cannot bend that way)."""
    first = solve_arm(S, P, hint)
    if first[2] < 2e-3 and np.linalg.norm(first[0] - S) > 0.3:
        return hint
    best, best_hint = (first[2] + (0.0 if np.linalg.norm(first[0] - S) > 0.3 else 1.0), None), hint
    for c in np.array(np.meshgrid([-1, 0, 1], [-1, 0, 1], [-1, 0, 1])).reshape(3, -1).T:
        if not c.any():
            continue
        h = c / np.linalg.norm(c)
        W, E, miss = solve_arm(S, P, h)
        score = miss + (0.0 if np.linalg.norm(W - S) > 0.3 else 1.0)
        if score < best[0]:
            best, best_hint = (score, None), h
    return best_hint


def _bump(t, tc):
    """0 -> 1 -> 0 over BUMP_S either side of tc (sin^2)."""
    return np.sin(np.pi * np.clip((t - (tc - BUMP_S)) / (2 * BUMP_S), 0.0, 1.0)) ** 2


def _pin_weight(d):
    """1 within PIN_HALF_S of the contact, easing to 0 over PIN_BEFORE_S before and PIN_AFTER_S after."""
    d = np.asarray(d, float)
    before = sm.mj((d + PIN_HALF_S + PIN_BEFORE_S) / PIN_BEFORE_S)
    after = sm.mj(1 - (d - PIN_HALF_S) / PIN_AFTER_S)
    return np.where(np.abs(d) <= PIN_HALF_S, 1.0, np.where(d < 0, before, after))


def _shoulder_estimate(xz, fwd, h):
    """Right shoulder at a contact from the body model (what body.player_pose gives, without running it)."""
    up = np.array([0, 1.0, 0])
    left = np.cross(up, fwd)
    lunge, air = float(h["lunge"]), 0.3 * float(h["jump"])
    root_g = np.array([xz[0], 0.0, xz[1]]) + 0.25 * lunge * fwd
    return root_g + up * (sm.HIP_Y - 0.3 * lunge + air + sm.TORSO) - left * sm.SHOULDER_HALF


def _toward_band(d):
    """Shift of the shoulder that brings the shoulder -> contact vector d to length REACH_MIN..REACH_MAX (zero if already there)."""
    n = np.linalg.norm(d)
    if n < 1e-6:
        return np.array([REACH_MIN, 0.0, 0.0])  # exactly at the shoulder: step back (the sign is arbitrary)
    return d * (1 - np.clip(n, REACH_MIN, REACH_MAX) / n)


def plan_reach(root, fac, t, hits):
    """Moves the body so each contact is REACH_MIN..REACH_MAX from the right shoulder: returns the root path with a horizontal bump
    per contact and the vertical shift per contact (a rise = hop / tiptoe, a negative one = crouch)."""
    root = root.copy()
    vert = []
    for h in hits:
        i = int(np.clip(frame_of(h["t"]), 0, len(t) - 1))
        dy = sum(v * float(_bump(h["t"], tc)) for tc, v in vert)
        S = _shoulder_estimate(root[i], fac[i], h) + [0, dy, 0]
        d = np.asarray(h["at"], float) - S
        move = _toward_band(d)
        lo, hi = np.searchsorted(t, [h["t"] - BUMP_S, h["t"] + BUMP_S])
        root[lo:hi, 0] += move[0] * _bump(t[lo:hi], h["t"])
        root[lo:hi, 1] += move[2] * _bump(t[lo:hi], h["t"])
        vert.append((h["t"], float(move[1])))
    return root, vert


def _shift_joints(J, t, shifts, fac):
    """Adds each contact's body shift (tc, vector) to joints J [F,17,3] in a bump around tc. A rise lifts everything (a hop); a crouch lowers
    the hips and above and re-bends the knees (the feet stay on the floor)."""
    up = np.array([0, 1.0, 0])
    for tc, v in shifts:
        if not np.any(v):
            continue
        lo, hi = np.searchsorted(t, [tc - BUMP_S, tc + BUMP_S])
        w = _bump(t[lo:hi], tc)[:, None]
        if v[1] >= 0:
            J[lo:hi] += w[:, None, :] * v
            continue
        horiz = np.array([v[0], 0.0, v[2]])
        J[lo:hi] += w[:, None, :] * horiz
        J[lo:hi, :13] += w[:, None, :] * v[1] * up  # head, shoulders, arms, hips come down
        for f in range(lo, hi):
            for hip, ank, knee in ((11, 15, 13), (12, 16, 14)):
                J[f, knee] = sm.ik(J[f, hip], J[f, ank], sm.THIGH, sm.SHIN, fac[f])


def _pin_racquet(J, t, hits, fac):
    """Right arm IK: in a window around each contact the sweet spot goes from where the swing puts it to exactly the shuttle."""
    best, which = np.zeros(len(t)), np.full(len(t), -1)
    for k, h in enumerate(hits):
        lo, hi = np.searchsorted(t, [h["t"] - PIN_HALF_S - PIN_BEFORE_S, h["t"] + PIN_HALF_S + PIN_AFTER_S])
        w = _pin_weight(t[lo:hi] - h["t"])
        better = w > best[lo:hi]
        best[lo:hi] = np.where(better, w, best[lo:hi])
        which[lo:hi] = np.where(better, k, which[lo:hi])
    misses, prev, picked = [], None, {}
    for f in np.nonzero(best > 0)[0]:
        nat = sweet_spot(J[f])
        c = np.asarray(hits[which[f]]["at"], float)
        P = nat + best[f] * (c - nat)
        W0 = J[f, 10] if prev is None or f - prev[0] > 1 else prev[1]
        hs = _right_hint(fac[f])
        k = which[f]
        if k not in picked:  # chosen once per contact at its contact frame, then blended in with the pin weight
            ic = int(np.clip(frame_of(hits[k]["t"]), 0, len(t) - 1))
            picked[k] = pick_hint(J[ic, 6], c, _right_hint(fac[ic]))
        hint = (1 - best[f]) * hs / np.linalg.norm(hs) + best[f] * picked[k] / np.linalg.norm(picked[k])
        W, E, miss = solve_arm(J[f, 6], P, hint, W0)
        J[f, 10], J[f, 8] = W, E
        prev = (f, W)
        if best[f] == 1.0:
            misses.append(miss)
    return misses


def bodies(tl, seed, stats=None):
    rng = np.random.default_rng(seed)
    F = len(tl["pos"])
    t = np.arange(F) / FPS
    joints = np.zeros((F, 2, 17, 4))
    for p, sign in enumerate((-1, 1)):
        hits = tl["hits"][p]
        root0 = tl["pos"][:, p]
        fac0 = facing_for(root0, sign, t, hits, FPS)
        root, vert = plan_reach(root0, fac0, t, hits)  # the body steps / lunges / hops to the contact
        fac = facing_for(root, sign, t, hits, FPS)
        lunges = [h["t"] - 0.3 for h in hits if h["lunge"]]
        jumps = [h["t"] - sm.JUMP_S / 2 for h in hits if h["jump"]]
        J = sm.player_pose(root, sign, t, FPS, swings_for(hits, rng), lunges, jumps, fac)
        # what is still out of reach (the body model differs a little from the estimate) is a rigid shift of the whole body in the bump
        shifts = []
        for (tc, dy), h in zip(vert, hits):
            i = int(np.clip(frame_of(tc), 0, F - 1))
            S = J[i, 6] + sum((v * float(_bump(tc, tc2)) for tc2, v in shifts), np.zeros(3)) + [0, dy, 0]
            d = np.asarray(h["at"], float) - S
            extra = _toward_band(d)
            shifts.append((tc, extra + [0, dy, 0]))
        _shift_joints(J, t, shifts, fac)
        miss = _pin_racquet(J, t, hits, fac)
        if stats is not None:
            stats.setdefault("miss", []).extend(miss)
        joints[:, p, :, :3] = J
        joints[:, p, :, 3] = 1.0
    return joints


def to_replay(log, out_path):
    """Writes the full replay.bin (bodies, shuttle, contact / landing / point events) for a MatchLog; returns the build dict."""
    tl = build(log)
    joints = bodies(tl, log.seed)
    write_replay(out_path, FPS, [
        ({"name": "joints3d", "layout": "x,y,z,conf", "keypoint_format": "coco17", "players": ["p0", "p1"]}, joints),
        ({"name": "shuttle", "layout": "x,y,z,vx,vy,vz,conf,flight_id"}, tl["shuttle"]),
        ({"name": "events", "layout": "t,type,player,x,y,z", "per_frame": False}, tl["events"]),
    ])
    return tl
