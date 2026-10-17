"""Match log -> replay.bin the viewers play (what tools/badsim/play_match.py wrote before the split, minus the `oso-imu` swings pass).

Layout: lead-in 1 s, then every rally (60 fps frames taken from the 120 Hz sim steps), then a 4 s gap between rallies in which the
players walk to the next service positions (the shuttle is hidden), so rallies are always more than the stats' 3 s rally gap apart.
The 120 Hz sim steps are rebuilt from the 30 Hz log: players by cubic Hermite through the logged positions and velocities (anchored at
the rally start and landing), the shuttle by re-integrating each flight from its hit event with the sim's own RK2 integrator.
Bodies: body.player_pose along the players' root paths, swings timed so the forward-swing peak is the contact (pitch tilted to the
contact height), facing the shuttle around a contact and the net otherwise, lunge = hip drop, jump = hip rise.
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
            cur = dict(start=e, hits=[], land=None, point=None)
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
        starts = [(k0, np.array(r["start"]["shuttle"]), np.zeros(3))] + [(h["k"], np.array(h["pos"]), np.array(h["v"])) for h in r["hits"]]
        for i, (k, p, v) in enumerate(starts):
            flights.append((k, starts[i + 1][0] if i + 1 < len(starts) else kL, p, v))
    # integrate all flights together, one RK2 step per round
    p = np.array([f[2] for f in flights])
    v = np.array([f[3] for f in flights])
    n = np.array([f[1] - f[0] for f in flights])
    rows = np.empty((n.max() + 1, len(flights), 6))
    for j in range(n.max() + 1):
        rows[j] = np.concatenate([p, v], -1)
        p, v = sim.rk2(p, v)
    for i, (k, end, _, _) in enumerate(flights):  # chronological: a flight's last row is replaced by the next flight's first
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
        shuttle += list(sh)
        pos += list(p)
        for h in r["hits"]:
            t = t_of(h["k"])
            at = np.array(h["pos"]) * [sgn, 1.0, sgn]
            events.append([t, 1, slot(h["p"]), *at])
            ev_hits[slot(h["p"])].append(dict(t=t, h=h["h"], lunge=h["lunge"], jump=h["jump"], at=at))
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
