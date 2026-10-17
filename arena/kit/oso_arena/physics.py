"""Shuttle flight model for the simulator: a numpy-only copy of the integrator in tools/shuttle_physics.py (the kit ships alone).
test_badsim.py asserts the constants and the integrator match tools/. The shot solvers (scipy) stay in tools/.
Original doc: shuttle flight model shared by the synthetic generator (synth_shuttle.py) and the fitter (shuttle3d.py).
State (p, v) in court meters; a = g - (g / vt^2) |v| v with g = (0, -9.81, 0). Quadratic drag with terminal velocity vt."""
import numpy as np

G = 9.81
GRAVITY = np.array([0.0, -G, 0.0])
VT = 6.8  # m/s, default terminal velocity (the fitter may use another one, bounded VT_RANGE)
VT_RANGE = (5.5, 8.0)
# Ground truth integrates at 1 ms; the fitter passes a coarser step (RK4 error at 1/120 s is ~1e-9 m on this ODE,
# checked in test_shuttle.py) because it integrates thousands of trajectories
H_TRUTH = 1e-3
H_FIT = 1 / 120


def accel(v, vt=VT):
    return GRAVITY - (G / vt**2) * np.linalg.norm(v, axis=-1, keepdims=True) * v


def _rk4(p, v, h, vt):
    k1v = accel(v, vt)
    k2v = accel(v + 0.5 * h * k1v, vt)
    k3v = accel(v + 0.5 * h * k2v, vt)
    k4v = accel(v + h * k3v, vt)
    k1p, k2p, k3p = v, v + 0.5 * h * k1v, v + 0.5 * h * k2v
    k4p = v + h * k3v
    return (p + h / 6 * (k1p + 2 * k2p + 2 * k3p + k4p), v + h / 6 * (k1v + 2 * k2v + 2 * k3v + k4v))


def _march(p, v, t0, ts, vt, h):
    """ts ascending (or descending for a backward march), all on one side of t0."""
    ps, vs, t = [], [], t0
    for target in ts:
        n = max(1, int(np.ceil(abs(target - t) / h - 1e-9)))
        step = (target - t) / n
        for _ in range(n):
            p, v = _rk4(p, v, step, vt)
        t = target
        ps.append(p)
        vs.append(v)
    return ps, vs


def simulate(p0, v0, t0, times, vt=VT, h=H_TRUTH):
    """Integrates from (p0, v0) at t0 to every time in `times` (any order, before or after t0; backward in time is
    plain integration of the same ODE). p0, v0 may carry leading batch dimensions (B, 3).
    Returns positions, velocities with shape (..., T, 3)."""
    p0, v0 = np.asarray(p0, float), np.asarray(v0, float)
    times = np.asarray(times, float)
    out_p = np.empty(p0.shape[:-1] + (len(times), 3))
    out_v = np.empty_like(out_p)
    order = np.argsort(times)
    fwd = [i for i in order if times[i] >= t0]
    bwd = [i for i in order[::-1] if times[i] < t0]
    for idx in (fwd, bwd):
        if not idx:
            continue
        ps, vs = _march(p0, v0, t0, times[idx], vt, h)
        out_p[..., idx, :] = np.stack(ps, axis=-2)
        out_v[..., idx, :] = np.stack(vs, axis=-2)
    return out_p, out_v
