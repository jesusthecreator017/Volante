"""Per-camera 2D shuttle detections -> 3D trajectory by fitting a drag-physics flight per shot -> replay.bin tracks
`shuttle` and `events` (format: docs/data-format.md).
Run: python shuttle3d.py --calib ../assets/calib/calib.json --shuttle2d ../out/synth/shuttle2d_a.npz ../out/synth/shuttle2d_b.npz
                         --replay ../assets/replay/replay.bin -o ../assets/replay/replay.bin [--camsync camsync.json]
(--camsync: cameras that are not frame-synchronized; camera B's detections are resampled to camera A's timestamps)
Steps: triangulate frames with both detections -> seed a flight from 5 consistent candidates -> grow it forward and
backward while detections agree with the physics fit (gap tolerant) -> final fit -> contacts between flights."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares, minimize_scalar
from scipy.spatial.transform import Rotation

import replay
import shuttle_physics as sp
from camsync import resample_shuttle
from triangulate import reprojection_error, triangulate

MIN_SCORE = 0.3
CAND_REPROJ_PX = 6.0
VOLUME = dict(x=9.0, z=7.0, y=(-0.1, 15.0))
SEED_N, SEED_SPAN_S = 5, 0.5
SEED_MEDIAN_PX = 4.0
GATE_PX, GATE_PER_S = 8.0, 120.0  # an observation joins a flight if within GATE_PX + GATE_PER_S * (s since the last one)
MAX_MISSES = 3  # consecutive observed frames that all disagree end a flight
CONSISTENT_PX = 6.0
REFIT_EVERY = 3
F_SCALE_PX = 3.0
# Terminal velocity is fitted per flight (bounded sp.VT_RANGE). While segmenting, a weak prior keeps short arcs from
# wandering; the final refit pulls every flight toward the clip's vt (weighted median) with a soft 0.3 m/s sigma,
# since real shots differ a little. The prior residual is in pixels: (dvt / sigma) * PRIOR_PX.
VT_PRIOR_GROW_SIGMA, VT_PRIOR_FINAL_SIGMA, PRIOR_PX = 1.5, 0.3, 1.5
MIN_OBS_FOR_CLIP_VT = 15
MAX_CONTACT_GAP_S = 0.25
MAX_CONTACT_DIST_M = 0.15
H = sp.H_FIT


def load_shuttle2d(path):
    z = np.load(path)
    return dict(uv=z["uv"].astype(np.float64), score=z["score"], t=z["t"], camera_id=str(z["camera_id"]),
                model=str(z["model"]))


class Rig:
    """Calibrated cameras: projection of court points to distorted pixels."""

    def __init__(self, calib):
        self.cams = []
        for c in calib["cameras"][:2]:
            rvec, _ = cv2.Rodrigues(np.array(c["R"]))
            self.cams.append((rvec, np.array(c["t"], float), np.array(c["K"], float), np.array(c["dist"], float)))

    def project(self, cam, pts):
        pts = np.asarray(pts, float)
        uv, _ = cv2.projectPoints(pts.reshape(-1, 1, 3), *self.cams[cam])
        return uv.reshape(pts.shape[:-1] + (2,))


class Flight:
    def __init__(self, obs):
        self.obs = sorted(obs)  # (frame, cam, u, v)
        self.t_ref = None
        self.p0 = self.v0 = None
        self.landed = False
        self.vt = sp.VT
        self.clip_vt = sp.VT

    def add(self, items):
        self.obs = sorted(set(self.obs) | set(items))

    def times(self, fps):
        return np.array([o[0] for o in self.obs]) / fps

    def predict(self, t):
        p, v = sp.simulate(self.p0, self.v0, self.t_ref, np.atleast_1d(t), self.vt, H)
        return p, v

    @property
    def first_frame(self):
        return self.obs[0][0]

    @property
    def last_frame(self):
        return self.obs[-1][0]


def fit_flight(fl, rig, fps, init=None, vt_prior=(sp.VT, VT_PRIOR_GROW_SIGMA)):
    """Least squares on (p0, v0, vt) at the first observation time, residual = 2D reprojection of every detection in its
    own camera (soft_l1) plus the soft vt prior (centre, sigma). Updates fl in place and returns the per-observation
    pixel errors."""
    obs = fl.obs
    fl.t_ref = obs[0][0] / fps
    ts = np.array([o[0] for o in obs]) / fps
    cam = np.array([o[1] for o in obs])
    uv = np.array([[o[2], o[3]] for o in obs])
    uniq, inv = np.unique(ts, return_inverse=True)

    def project(P):  # P [B, U, 3] -> [B, N, 2] pixel positions for each observation
        out = np.empty(P.shape[:-2] + (len(obs), 2))
        for c in (0, 1):
            m = cam == c
            if m.any():
                out[..., m, :] = rig.project(c, P[..., inv[m], :])
        return out

    c_vt, sig_vt = vt_prior

    def data_resid(P):
        return (project(P) - uv).reshape(P.shape[0], -1)

    def resid(x):
        P, _ = sp.simulate(x[:3], x[3:6], fl.t_ref, uniq, x[6], H)
        return np.append(data_resid(P[None])[0], (x[6] - c_vt) / sig_vt * PRIOR_PX)

    def jac(x):
        eps = np.array([1e-4] * 3 + [1e-3] * 3 + [1e-3])
        X = x + np.vstack([np.zeros(7), np.diag(eps)])
        P, _ = sp.simulate(X[:, :3], X[:, 3:6], fl.t_ref, uniq, X[:, 6:7], H)
        R = np.concatenate([data_resid(P), ((X[:, 6] - c_vt) / sig_vt * PRIOR_PX)[:, None]], axis=1)
        return ((R[1:] - R[0]) / eps[:, None]).T

    if init is None:
        init = (fl.p0, fl.v0)
    x0 = np.concatenate([init[0], init[1], [fl.vt]])
    lo, hi = np.array([-np.inf] * 6 + [sp.VT_RANGE[0]]), np.array([np.inf] * 6 + [sp.VT_RANGE[1]])
    r = least_squares(resid, x0, jac=jac, bounds=(lo, hi), loss="soft_l1", f_scale=F_SCALE_PX, max_nfev=30,
                      x_scale=[1, 1, 1, 5, 5, 5, 0.5])
    fl.p0, fl.v0, fl.vt = r.x[:3], r.x[3:6], float(r.x[6])
    return np.linalg.norm(resid(r.x)[:-1].reshape(-1, 2), axis=1)


def floor_time(fl, fps, horizon=4.0):
    """First time after the last observation the flight reaches y = 0, or None."""
    t0 = fl.last_frame / fps
    ts = t0 + np.arange(0, horizon, 0.01)
    p, _ = fl.predict(ts)
    below = np.flatnonzero(p[:, 1] <= 0)
    if len(below) == 0:
        return None
    i = below[0]
    if i == 0:
        return t0
    # linear refine within the 10 ms step
    return float(ts[i - 1] + (ts[i] - ts[i - 1]) * p[i - 1, 1] / (p[i - 1, 1] - p[i, 1]))


def candidates(calib, dets, fps):
    """Frames with both detections whose triangulation reprojects within CAND_REPROJ_PX in both views and lies in the
    volume: (frames, xyz)."""
    a, b = dets
    both = np.flatnonzero((a["score"] >= MIN_SCORE) & (b["score"] >= MIN_SCORE))
    if len(both) == 0:
        return np.array([], int), np.zeros((0, 3))
    p = triangulate(calib, a["uv"][both], b["uv"][both])
    ea, eb = reprojection_error(calib, p, a["uv"][both], b["uv"][both])
    ok = (ea < CAND_REPROJ_PX) & (eb < CAND_REPROJ_PX)
    ok &= (np.abs(p[:, 0]) < VOLUME["x"]) & (np.abs(p[:, 2]) < VOLUME["z"])
    ok &= (p[:, 1] > VOLUME["y"][0]) & (p[:, 1] < VOLUME["y"][1])
    return both[ok], p[ok]


def frame_obs(dets, f):
    return [(f, c, float(d["uv"][f, 0]), float(d["uv"][f, 1])) for c, d in enumerate(dets) if d["score"][f] >= MIN_SCORE]


def seed_flights_from(cf, cp, start, dets, rig, fps, vt_hint=sp.VT):
    """First seed at or after candidate index `start`: SEED_N candidates within SEED_SPAN_S whose physics fit has
    median error < SEED_MEDIAN_PX. Returns (Flight, index after the seed window) or (None, len)."""
    i = start
    while i + SEED_N <= len(cf):
        if (cf[i + SEED_N - 1] - cf[i]) / fps > SEED_SPAN_S:
            i += 1
            continue
        obs = [o for f in cf[i:i + SEED_N] for o in frame_obs(dets, f)]
        fl = Flight(obs)
        fl.vt = vt_hint
        dt = (cf[i + SEED_N - 1] - cf[i]) / fps
        v = (cp[i + SEED_N - 1] - cp[i]) / dt - 0.5 * sp.GRAVITY * dt
        err = fit_flight(fl, rig, fps, init=(cp[i], v), vt_prior=(vt_hint, VT_PRIOR_GROW_SIGMA))
        if np.median(err) < SEED_MEDIAN_PX:
            return fl, i + SEED_N
        i += 1
    return None, len(cf)


def grow(fl, dets, rig, fps, direction, f_limit):
    """Extends the flight frame by frame (direction +1 forward up to f_limit, -1 backward down to f_limit) with the
    detections that agree with the current fit. A run of MAX_MISSES observed frames that all disagree stops it; a
    forward flight also stops at the floor. Refits every REFIT_EVERY accepted frames."""
    F = len(dets[0]["score"])
    f = (fl.last_frame if direction > 0 else fl.first_frame) + direction
    t_last = f / fps - direction / fps
    misses, accepted = 0, 0
    while 0 <= f < F and (f <= f_limit if direction > 0 else f >= f_limit):
        t = f / fps
        p, _ = fl.predict(t)
        if direction > 0 and p[0, 1] <= 0.0:
            fl.landed = True
            break
        items = frame_obs(dets, f)
        if items:
            gate = GATE_PX + GATE_PER_S * abs(t - t_last)
            good = [o for o in items if np.linalg.norm(rig.project(o[1], p[0]) - np.array(o[2:])) < gate]
            if good:
                fl.add(good)
                t_last, misses, accepted = t, 0, accepted + 1
                if accepted % REFIT_EVERY == 0:
                    fit_flight(fl, rig, fps)
            else:
                misses += 1
                if misses >= MAX_MISSES:
                    break
        f += direction
    fit_flight(fl, rig, fps)


def find_flights(calib, dets, rig, fps):
    cf, cp = candidates(calib, dets, fps)
    F = len(dets[0]["score"])
    flights, i, floor, vt_hint = [], 0, 0, sp.VT
    while True:
        fl, i = seed_flights_from(cf, cp, i, dets, rig, fps, vt_hint)
        if fl is None:
            break
        grow(fl, dets, rig, fps, +1, F - 1)
        grow(fl, dets, rig, fps, -1, floor)
        flights.append(fl)
        if len(fl.obs) >= MIN_OBS_FOR_CLIP_VT:
            vt_hint = fl.vt
        floor = fl.last_frame + 1
        i = int(np.searchsorted(cf, floor))
    # Clip vt: median over flights weighted by observation count; then the final refit with the soft prior around it
    long = [f for f in flights if len(f.obs) >= MIN_OBS_FOR_CLIP_VT]
    if long:
        order = np.argsort([f.vt for f in long])
        w = np.cumsum([len(long[k].obs) for k in order])
        clip = long[order[int(np.searchsorted(w, w[-1] / 2))]].vt
    else:
        clip = sp.VT
    for fl in flights:
        fl.clip_vt = clip
        fit_flight(fl, rig, fps, vt_prior=(clip, VT_PRIOR_FINAL_SIGMA))
    return flights


def contact_between(a, b, fps):
    """Closest approach of flight a's forward extrapolation and flight b's backward extrapolation within +-2 frames
    of the boundary. Returns (t, position, distance)."""
    lo, hi = a.last_frame / fps - 2 / fps, b.first_frame / fps + 2 / fps

    def dist(t):
        return np.linalg.norm(a.predict(t)[0][0] - b.predict(t)[0][0])

    ts = np.linspace(lo, hi, 41)
    d = [dist(t) for t in ts]
    k = int(np.argmin(d))
    r = minimize_scalar(dist, bounds=(ts[max(k - 1, 0)], ts[min(k + 1, 40)]), method="bounded", options={"xatol": 1e-6})
    t = float(r.x)
    return t, 0.5 * (a.predict(t)[0][0] + b.predict(t)[0][0]), float(r.fun)


def nearest_player(replay_tracks, fps, t, pos):
    """Player whose racquet head (racquet track, conf > 0) or else right wrist is nearest `pos` at time t."""
    j = replay_tracks["joints3d"]
    f = min(max(int(round(t * fps)), 0), len(j) - 1)
    best, who = 1e9, 0
    for p in range(j.shape[1]):
        c = j[f, p, 10, :3]
        r = replay_tracks.get("racquet")
        if r is not None and r[f, p, 7] > 0:
            q = r[f, p, 3:7]
            c = r[f, p, :3] + 0.53 * Rotation.from_quat(q).apply([0, 1.0, 0])
        d = np.linalg.norm(c - pos)
        if d < best:
            best, who = d, p
    return who


def build_tracks(flights, dets, rig, fps, F, tracks):
    """Returns (shuttle [F, 8], events [N, 6], info). Frame f takes the flight whose contact-bounded time span holds it."""
    spans, events = [], []
    for k, fl in enumerate(flights):
        spans.append([fl.first_frame / fps, fl.last_frame / fps])
    for k in range(len(flights) - 1):
        a, b = flights[k], flights[k + 1]
        if a.landed or (b.first_frame - a.last_frame) / fps > MAX_CONTACT_GAP_S:
            continue
        t, pos, d = contact_between(a, b, fps)
        if d > MAX_CONTACT_DIST_M:
            continue
        spans[k][1], spans[k + 1][0] = t, t
        events.append([t, 1, nearest_player(tracks, fps, t, pos), *pos])
    for fl, span in zip(flights, spans):
        if fl.landed:
            tl = floor_time(fl, fps)
            p, _ = fl.predict(tl)
            span[1] = tl
            events.append([tl, 2, -1, p[0, 0], 0.0, p[0, 2]])
    out = np.zeros((F, 8), np.float32)
    out[:, 7] = -1
    tf = np.arange(F) / fps
    for k, (fl, (t0, t1)) in enumerate(zip(flights, spans)):
        m = (tf >= t0) & (tf <= t1)
        if not m.any():
            continue
        p, v = fl.predict(tf[m])
        out[m, :3], out[m, 3:6], out[m, 7] = p, v, k
        out[m, 6] = 0.5
        frames = np.flatnonzero(m)
        for f in frames:  # conf 1 where some detection of the frame agrees with the fit
            pf = out[f, :3]
            for o in frame_obs(dets, f):
                if np.linalg.norm(rig.project(o[1], pf) - np.array(o[2:])) < CONSISTENT_PX:
                    out[f, 6] = 1.0
                    break
    ev = np.array(sorted(events), np.float32).reshape(-1, 6)
    return out, ev


def shuttle3d(calib, dets, tracks, camsync=None):
    fps = calib["cameras"][0]["fps"]
    if camsync is None:
        assert all(np.abs(d["t"] - dets[0]["t"]).max() < 1e-3 for d in dets), "cameras not frame-synchronized (pass --camsync)"
    else:
        dets = [dets[0], resample_shuttle(dets[1], camsync, dets[0]["t"])]
    rig = Rig(calib)
    flights = find_flights(calib, dets, rig, fps)
    shuttle, events = build_tracks(flights, dets, rig, fps, len(dets[0]["t"]), tracks)
    return shuttle, events, flights


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", type=Path, required=True)
    ap.add_argument("--shuttle2d", type=Path, nargs=2, required=True)
    ap.add_argument("--replay", type=Path, required=True)
    ap.add_argument("--camsync", type=Path, help="camsync.json: resample camera B to camera A's clock")
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()
    calib = json.loads(args.calib.read_text())
    dets = [load_shuttle2d(p) for p in args.shuttle2d]
    assert [d["camera_id"] for d in dets] == [c["id"] for c in calib["cameras"][:2]], "camera order"
    header, tracks = replay.read(args.replay)
    shuttle, events, flights = shuttle3d(calib, dets, tracks, json.loads(args.camsync.read_text()) if args.camsync else None)
    kept = [({k: v for k, v in m.items() if k not in ("dtype", "shape", "offset")}, tracks[m["name"]])
            for m in header["tracks"] if m["name"] not in ("shuttle", "events")]
    kept += [({"name": "shuttle", "layout": "x,y,z,vx,vy,vz,conf,flight_id"}, shuttle),
             ({"name": "events", "layout": "t,type,player,x,y,z", "per_frame": False}, events)]
    replay.write(args.out, header["fps"], kept, header.get("start_time", 0.0), header["coordinate_system"],
                 frame_count=header["frame_count"])
    n1 = int((shuttle[:, 6] == 1).sum())
    print(f"wrote {args.out}: {len(flights)} flights, {n1} conf-1 and {int((shuttle[:, 6] == 0.5).sum())} filled frames, "
          f"{int((events[:, 1] == 1).sum())} contacts, {int((events[:, 1] == 2).sum())} landings")


if __name__ == "__main__":
    main()
