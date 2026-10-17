//! Gyro bias calibration and 6-axis Madgwick orientation.
//!
//! Internally Madgwick runs in a z-up world; the output is rotated into the court frame (+Y up).
//! Yaw is unobservable from gravity alone: the output heading starts arbitrary and drifts, and
//! `correct.rs` anchors it to the video.

use super::packet::Sample;
use super::{G, Quat, Vec3, from_axis_angle, from_two_vectors, mul, normalize, rotate, slerp};

pub const DEFAULT_BETA: f64 = 0.04;
/// Longer than this between samples = packets were lost: tilt is re-initialised from the accelerometer
pub const MAX_DT: f64 = 0.05;
const LOW_CONF: f32 = 0.5;
/// The accelerometer only corrects tilt while its magnitude is within this of 1 g (a swing's centripetal force is not gravity)
const ACC_GATE_G: f64 = 0.1;
// A hole of 4+ packets (8 ms at 500 Hz) during a fast swing leaves an integration error the Hermite fill only partly removes
const LOST_LOW_DT_FACTOR: f64 = 4.0;
/// Both passes within this of a disturbance = low confidence
const LOW_CONF_WINDOW_S: f64 = 0.5;
/// Forward/backward blend sharpness, seconds
const BLEND_TAU_S: f64 = 1.0;
const FAR_S: f64 = 30.0;

// Stationary detection, in blocks of BLOCK samples (0.1 s at 500 Hz)
const BLOCK: usize = 50;
const MIN_BLOCKS: usize = 10;
// ponytail: the spec's "|gyro| < 3 dps" fails with a 1-3 dps/axis bias on every axis (norm up to 5 dps), so a block is
// still when its gyro *spread* is small and its mean is within a plausible bias; upgrade to a held-out bias check if real
// parts turn out to have larger offsets.
const STILL_GYRO_STD_RAD: f64 = 2.0 * std::f64::consts::PI / 180.0;
const STILL_GYRO_MEAN_RAD: f64 = 6.0 * std::f64::consts::PI / 180.0;
const STILL_ACC_G: f64 = 0.05;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Window {
    /// Sample indices, end exclusive
    pub start: usize,
    pub end: usize,
}

/// First run of >= 1 s where the sensor is held still. The final block of the run is dropped: it may contain the
/// first motion.
pub fn find_stationary(samples: &[Sample]) -> Option<Window> {
    let mut run_start = None;
    let n_blocks = samples.len() / BLOCK;
    for b in 0..=n_blocks {
        let still = b < n_blocks && block_is_still(&samples[b * BLOCK..(b + 1) * BLOCK]);
        match (still, run_start) {
            (true, None) => run_start = Some(b),
            (false, Some(s)) => {
                if b - s >= MIN_BLOCKS {
                    return Some(Window {
                        start: s * BLOCK,
                        end: (b - 1) * BLOCK,
                    });
                }
                run_start = None;
            }
            _ => {}
        }
    }
    None
}

fn block_is_still(block: &[Sample]) -> bool {
    // A lost-packet gap inside the block stretches it past its nominal 100 ms
    if block[block.len() - 1].t_dev - block[0].t_dev > 0.12 {
        return false;
    }
    let n = block.len() as f64;
    for axis in 0..3 {
        let stat = |f: &dyn Fn(&Sample) -> f64| {
            let mean = block.iter().map(f).sum::<f64>() / n;
            let var = block.iter().map(|s| (f(s) - mean).powi(2)).sum::<f64>() / n;
            (mean, var.sqrt())
        };
        let (gm, gs) = stat(&|s| s.gyro[axis] as f64);
        let (_, a_std) = stat(&|s| s.acc[axis] as f64 / G);
        if gs > STILL_GYRO_STD_RAD || gm.abs() > STILL_GYRO_MEAN_RAD || a_std > STILL_ACC_G {
            return false;
        }
    }
    let mean = mean_acc(block);
    (super::norm(mean) / G - 1.0).abs() < STILL_ACC_G
}

pub(super) fn mean_acc(s: &[Sample]) -> Vec3 {
    let n = s.len() as f64;
    std::array::from_fn(|a| s.iter().map(|x| x.acc[a] as f64).sum::<f64>() / n)
}

/// Mean gyro over the window: the constant bias, rad/s.
pub fn gyro_bias(samples: &[Sample], w: Window) -> Vec3 {
    let s = &samples[w.start..w.end];
    std::array::from_fn(|a| s.iter().map(|x| x.gyro[a] as f64).sum::<f64>() / s.len().max(1) as f64)
}

#[derive(Debug, Clone, Copy)]
pub struct Params {
    pub beta: f64,
    /// Forward-backward fusion (false: forward only)
    pub two_pass: bool,
    /// Seconds between samples at the nominal rate (1 / rate_hz)
    pub nominal_dt: f64,
}

impl Default for Params {
    fn default() -> Self {
        Params {
            beta: DEFAULT_BETA,
            two_pass: true,
            nominal_dt: 0.002,
        }
    }
}

/// One Madgwick step. `q` = (w, x, y, z), sensor -> z-up world. `acc` any scale (only its direction is used).
pub fn madgwick_step(q: &mut [f64; 4], gyro: Vec3, acc: Option<Vec3>, dt: f64, beta: f64) {
    let [q0, q1, q2, q3] = *q;
    let mut qd = [
        0.5 * (-q1 * gyro[0] - q2 * gyro[1] - q3 * gyro[2]),
        0.5 * (q0 * gyro[0] + q2 * gyro[2] - q3 * gyro[1]),
        0.5 * (q0 * gyro[1] - q1 * gyro[2] + q3 * gyro[0]),
        0.5 * (q0 * gyro[2] + q1 * gyro[1] - q2 * gyro[0]),
    ];
    if let Some(a) = acc {
        let n = super::norm(a);
        if n > 1e-9 {
            let (ax, ay, az) = (a[0] / n, a[1] / n, a[2] / n);
            // Gravity direction predicted by q (third row of its rotation matrix) minus the measurement
            let f1 = 2.0 * (q1 * q3 - q0 * q2) - ax;
            let f2 = 2.0 * (q0 * q1 + q2 * q3) - ay;
            let f3 = 2.0 * (0.5 - q1 * q1 - q2 * q2) - az;
            let g = [
                -2.0 * q2 * f1 + 2.0 * q1 * f2,
                2.0 * q3 * f1 + 2.0 * q0 * f2 - 4.0 * q1 * f3,
                -2.0 * q0 * f1 + 2.0 * q3 * f2 - 4.0 * q2 * f3,
                2.0 * q1 * f1 + 2.0 * q2 * f2,
            ];
            let gn = g.iter().map(|c| c * c).sum::<f64>().sqrt();
            if gn > 1e-12 {
                for i in 0..4 {
                    qd[i] -= beta * g[i] / gn;
                }
            }
        }
    }
    let mut r = [
        q0 + qd[0] * dt,
        q1 + qd[1] * dt,
        q2 + qd[2] * dt,
        q3 + qd[3] * dt,
    ];
    let n = r.iter().map(|c| c * c).sum::<f64>().sqrt();
    r = r.map(|c| c / n);
    *q = r;
}

pub(super) fn to_madgwick([x, y, z, w]: Quat) -> [f64; 4] {
    [w, x, y, z]
}

pub(super) fn from_madgwick([w, x, y, z]: [f64; 4]) -> Quat {
    [x, y, z, w]
}

/// Z-up Madgwick world -> court frame (+Y up): rotate -90 deg about X.
pub(super) fn world_to_court() -> Quat {
    from_axis_angle([1.0, 0.0, 0.0], -std::f64::consts::FRAC_PI_2)
}

/// Orientation track on the device clock.
#[derive(Debug, Default)]
pub struct Track {
    pub t: Vec<f64>,
    /// Sensor -> court (+Y up) orientation, `[x, y, z, w]`, arbitrary heading
    pub q: Vec<Quat>,
    /// Bias-corrected angular rate in the sensor frame, rad/s
    pub w: Vec<Vec3>,
    /// 1 trusted, 0.5 low (saturation or lost packets)
    pub conf: Vec<f32>,
}

/// State at one instant
#[derive(Debug, Clone, Copy)]
pub struct At {
    pub q: Quat,
    pub w: Vec3,
    pub conf: f32,
}

impl Track {
    /// Orientation at device time `t` (slerp between the bracketing samples). None outside the recording or
    /// inside a lost-packet gap longer than MAX_DT.
    pub fn at(&self, t: f64) -> Option<At> {
        let i = self.t.partition_point(|&x| x <= t);
        if i == 0 {
            return None;
        }
        if i == self.t.len() {
            return (t == self.t[i - 1]).then(|| self.get(i - 1));
        }
        let (t0, t1) = (self.t[i - 1], self.t[i]);
        if t1 - t0 > MAX_DT {
            return None;
        }
        let f = (t - t0) / (t1 - t0);
        let (a, b) = (self.get(i - 1), self.get(i));
        Some(At {
            q: slerp(a.q, b.q, f),
            w: std::array::from_fn(|k| a.w[k] + (b.w[k] - a.w[k]) * f),
            conf: a.conf.min(b.conf),
        })
    }

    fn get(&self, i: usize) -> At {
        At {
            q: self.q[i],
            w: self.w[i],
            conf: self.conf[i],
        }
    }
}

/// Advances `q` from `prev` to `cur`. `prev2` / `next` are the neighbours (only used to fit the Hermite curve over a
/// hole). `sign` -1 = backward pass (negated rates).
#[allow(clippy::too_many_arguments)]
pub(super) fn step(
    q: &mut [f64; 4],
    prev2: Option<&Sample>,
    prev: Option<&Sample>,
    cur: &Sample,
    next: Option<&Sample>,
    bias: Vec3,
    sign: f64,
    params: Params,
) {
    let Some(prev) = prev else { return };
    let up = [0.0, 0.0, 1.0];
    let gyro_of = |s: &Sample| -> Vec3 {
        std::array::from_fn(|a| sign * (s.gyro[a] as f64 - bias[a]))
    };
    let dt = cur.t_dev - prev.t_dev;
    let acc = cur.acc.map(|c| c as f64);
    if dt > MAX_DT {
        // Lost packets: keep the heading, redo the tilt from gravity
        let qm = from_madgwick(*q);
        let tilt = from_two_vectors(rotate(qm, acc), up);
        *q = to_madgwick(normalize(mul(tilt, qm)));
    } else if dt > 0.0 {
        let gated = (super::norm(acc) / G - 1.0).abs() <= ACC_GATE_G;
        let (w0, w1) = (gyro_of(prev), gyro_of(cur));
        if dt <= 1.5 * params.nominal_dt {
            // Trapezoid
            let rate = std::array::from_fn(|a| 0.5 * (w0[a] + w1[a]));
            madgwick_step(q, rate, gated.then_some(acc), dt, params.beta);
        } else {
            // Lost packets: the rate between the neighbours is a cubic Hermite curve through their values and
            // slopes, integrated in nominal-size sub-steps (one Euler step over 40 ms would be far off)
            let slope = |a: &Sample, b: &Sample| -> Vec3 {
                let h = b.t_dev - a.t_dev;
                if h > 0.0 && h <= 1.5 * params.nominal_dt {
                    let (wa, wb) = (gyro_of(a), gyro_of(b));
                    std::array::from_fn(|k| (wb[k] - wa[k]) / h)
                } else {
                    [0.0; 3]
                }
            };
            let m0 = prev2.map_or([0.0; 3], |p2| slope(p2, prev));
            let m1 = next.map_or([0.0; 3], |n| slope(cur, n));
            let n = (dt / params.nominal_dt).round().max(1.0) as usize;
            for k in 0..n {
                let u = (k as f64 + 0.5) / n as f64;
                let (u2, u3) = (u * u, u * u * u);
                let (h00, h10, h01, h11) = (
                    2.0 * u3 - 3.0 * u2 + 1.0,
                    u3 - 2.0 * u2 + u,
                    3.0 * u2 - 2.0 * u3,
                    u3 - u2,
                );
                let rate = std::array::from_fn(|a| {
                    h00 * w0[a] + h10 * dt * m0[a] + h01 * w1[a] + h11 * dt * m1[a]
                });
                let last = k + 1 == n;
                madgwick_step(
                    q,
                    rate,
                    (gated && last).then_some(acc),
                    dt / n as f64,
                    params.beta,
                );
            }
        }
    }
}

/// One Madgwick pass over `samples` (in the order given; the backward pass feeds reversed time and negated rates).
/// Returns the Madgwick-frame quaternion (w, x, y, z) after each sample.
fn pass(samples: &[Sample], bias: Vec3, q0: [f64; 4], sign: f64, params: Params) -> Vec<[f64; 4]> {
    let mut q = q0;
    let mut out = Vec::with_capacity(samples.len());
    // The backward pass is handed reversed samples with negated times, so time runs forward in list order
    for i in 0..samples.len() {
        let at = |j: Option<usize>| j.and_then(|j| samples.get(j));
        step(
            &mut q,
            at(i.checked_sub(2)),
            at(i.checked_sub(1)),
            &samples[i],
            at(Some(i + 1)),
            bias,
            sign,
            params,
        );
        out.push(q);
    }
    out
}

/// Disturbance intervals (start, end) in device time: a saturated gyro sample, or a hole of 4+ packets. Integration
/// error enters a pass there and persists after it.
fn disturbances(samples: &[Sample], nominal_dt: f64) -> Vec<(f64, f64)> {
    let mut d = Vec::new();
    for (i, s) in samples.iter().enumerate() {
        if i > 0 && s.t_dev - samples[i - 1].t_dev >= LOST_LOW_DT_FACTOR * nominal_dt {
            d.push((samples[i - 1].t_dev, s.t_dev));
        }
        if s.saturated {
            d.push((s.t_dev, s.t_dev));
        }
    }
    d.sort_by(|a, b| a.0.total_cmp(&b.0));
    d
}

/// Fuses all samples. `init_acc`: accelerometer direction at rest (the stationary window's mean) for the initial tilt.
///
/// Offline forward-backward fusion: Madgwick runs forward from the initial tilt and backward (reversed time, negated rates)
/// from the final orientation, and each sample blends the two by slerp. A pass is only as good as the time since its last
/// disturbance (saturation, lost packets: error enters there and recovers slowly), so the forward pass is trusted
/// after a disturbance only once it has been clean longer than the backward pass will be before the next one:
/// `w_fwd = sigmoid(2 (since_last - until_next) / BLEND_TAU)`.
pub fn run(samples: &[Sample], bias: Vec3, init_acc: Vec3, params: Params) -> Track {
    let mut track = Track::default();
    if samples.is_empty() {
        return track;
    }
    let p = world_to_court();
    let up = [0.0, 0.0, 1.0];
    let q0 = to_madgwick(from_two_vectors(init_acc, up));
    let fwd = pass(samples, bias, q0, 1.0, params);
    let dist = disturbances(samples, params.nominal_dt);
    let q = if params.two_pass {
        // Backward starts at the forward result's heading, with tilt re-read from the last accelerometer sample
        // (if it reads 1 g) so a disturbance late in the clip does not poison the whole backward pass
        let last = samples.len() - 1;
        let acc = samples[last].acc.map(|c| c as f64);
        let qf = from_madgwick(fwd[last]);
        let qb0 = if (super::norm(acc) / G - 1.0).abs() <= ACC_GATE_G {
            to_madgwick(normalize(mul(from_two_vectors(rotate(qf, acc), up), qf)))
        } else {
            fwd[last]
        };
        let rev: Vec<Sample> = samples
            .iter()
            .rev()
            .map(|s| Sample {
                t_dev: -s.t_dev,
                ..*s
            })
            .collect();
        let mut bwd = pass(&rev, bias, qb0, -1.0, params);
        bwd.reverse();
        (0..samples.len())
            .map(|i| {
                let (since, until) = distances(&dist, samples[i].t_dev);
                let w_fwd = 1.0 / (1.0 + (-2.0 * (since - until) / BLEND_TAU_S).exp());
                // Slerp from forward (weight 1 at t = 0 of the blend) toward backward
                slerp(from_madgwick(fwd[i]), from_madgwick(bwd[i]), 1.0 - w_fwd)
            })
            .collect::<Vec<_>>()
    } else {
        fwd.iter().map(|&q| from_madgwick(q)).collect()
    };
    for (i, s) in samples.iter().enumerate() {
        let (since, until) = distances(&dist, s.t_dev);
        // Both passes recently disturbed: nothing to lean on
        let low = if params.two_pass {
            since < LOW_CONF_WINDOW_S && until < LOW_CONF_WINDOW_S
        } else {
            since < LOW_CONF_WINDOW_S
        };
        track.t.push(s.t_dev);
        track.q.push(normalize(mul(p, q[i])));
        track
            .w
            .push(std::array::from_fn(|a| s.gyro[a] as f64 - bias[a]));
        track.conf.push(if low { LOW_CONF } else { 1.0 });
    }
    track
}

/// (time since the end of the last disturbance at or before `t`, time until the start of the next one at or after
/// `t`), each capped at `FAR_S`.
fn distances(dist: &[(f64, f64)], t: f64) -> (f64, f64) {
    let mut since = FAR_S;
    let mut until = FAR_S;
    for &(a, b) in dist {
        if b <= t {
            since = since.min(t - b);
        }
        if a >= t {
            until = until.min(a - t);
            break;
        }
        // Inside an interval (a < t < b): disturbed in both directions
        if a < t && t < b {
            return (0.0, 0.0);
        }
    }
    (since, until)
}

/// Convenience used by the CLI and tests: stationary window -> bias -> fusion.
/// Returns None when no stationary window exists.
pub fn calibrate_and_run(samples: &[Sample], params: Params) -> Option<(Track, Vec3, Window)> {
    let w = find_stationary(samples)?;
    let bias = gyro_bias(samples, w);
    let acc = mean_acc(&samples[w.start..w.end]);
    Some((run(samples, bias, acc, params), bias, w))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::imu::{angle_between, from_axis_angle};

    fn sample(t: f64, acc: [f64; 3], gyro: [f64; 3]) -> Sample {
        Sample {
            t_dev: t,
            acc: acc.map(|c| c as f32),
            gyro: gyro.map(|c| c as f32),
            saturated: false,
        }
    }

    fn level(n: usize, gyro: Vec3) -> Vec<Sample> {
        (0..n)
            .map(|i| sample(i as f64 * 0.002, [0.0, 0.0, G], gyro))
            .collect()
    }

    #[test]
    fn stationary_level_sensor_stays_level() {
        let s = level(2000, [0.0; 3]);
        let t = run(&s, [0.0; 3], [0.0, 0.0, G], Params::default());
        // Sensor z (up) -> court +Y
        let up = rotate(*t.q.last().unwrap(), [0.0, 0.0, 1.0]);
        assert!((up[1] - 1.0).abs() < 1e-9, "{up:?}");
        assert!(angle_between(t.q[0], *t.q.last().unwrap()) < 1e-9);
    }

    #[test]
    fn tilted_start_converges_to_gravity() {
        // Sensor x axis points up; filter started level must pull tilt in
        let s: Vec<_> = (0..3000)
            .map(|i| sample(i as f64 * 0.002, [G, 0.0, 0.0], [0.0; 3]))
            .collect();
        let t = run(
            &s,
            [0.0; 3],
            [0.0, 0.0, G],
            Params {
                beta: 0.5,
                ..Params::default()
            },
        );
        let up = rotate(*t.q.last().unwrap(), [1.0, 0.0, 0.0]);
        assert!((up[1] - 1.0).abs() < 1e-3, "{up:?}");
    }

    #[test]
    fn constant_rotation_about_z_integrates() {
        // 90 deg/s about the vertical for 1 s (level, so the accelerometer agrees)
        let w = 90f64.to_radians();
        let s = level(501, [0.0, 0.0, w]);
        let t = run(&s, [0.0; 3], [0.0, 0.0, G], Params::default());
        let got = *t.q.last().unwrap();
        // About the vertical = court +Y; the start orientation maps sensor z to court Y
        let want = mul(from_axis_angle([0.0, 1.0, 0.0], 90f64.to_radians()), t.q[0]);
        assert!(
            angle_between(got, want) < 0.5f64.to_radians(),
            "{}",
            angle_between(got, want).to_degrees()
        );
        // Bias is subtracted
        let s = level(501, [0.0, 0.0, w + 0.1]);
        let t = run(&s, [0.0, 0.0, 0.1], [0.0, 0.0, G], Params::default());
        assert!(angle_between(*t.q.last().unwrap(), want) < 0.5f64.to_radians());
    }

    #[test]
    fn backward_pass_alone_agrees_with_forward_on_constant_rotation() {
        // 90 deg/s about the vertical for 2 s: the backward pass, started from the forward pass's final
        // orientation, must walk back to the forward start (gyro negated, time reversed)
        let s = level(1001, [0.0, 0.0, 90f64.to_radians()]);
        let q0 = [1.0, 0.0, 0.0, 0.0];
        let fwd = pass(&s, [0.0; 3], q0, 1.0, Params::default());
        let rev: Vec<Sample> = s
            .iter()
            .rev()
            .map(|x| Sample {
                t_dev: -x.t_dev,
                ..*x
            })
            .collect();
        let mut bwd = pass(
            &rev,
            [0.0; 3],
            *fwd.last().unwrap(),
            -1.0,
            Params::default(),
        );
        bwd.reverse();
        for i in [0, 250, 500, 750, 1000] {
            let e = angle_between(from_madgwick(fwd[i]), from_madgwick(bwd[i])).to_degrees();
            assert!(e < 0.5, "sample {i}: {e} deg");
        }
        assert!(
            angle_between(from_madgwick(fwd[0]), from_madgwick(fwd[1000])) > 1.0,
            "the rotation must be real"
        );
    }

    #[test]
    fn finds_stationary_window_and_bias() {
        // 3 s still with a gyro bias of ~3 dps on every axis, then motion
        let bias = [0.05, -0.04, 0.03];
        let mut s = level(1500, bias);
        for i in 1500..1600 {
            s.push(sample(i as f64 * 0.002, [3.0, 0.0, G], [2.0, 0.0, 0.0]));
        }
        let w = find_stationary(&s).unwrap();
        assert_eq!(w.start, 0);
        assert!(w.end >= 1400 && w.end <= 1500, "{w:?}");
        let b = gyro_bias(&s, w);
        assert!(
            b.iter().zip(bias).all(|(a, e)| (a - e).abs() < 1e-6),
            "{b:?}"
        );
        // Too short, or never still
        assert!(find_stationary(&level(400, [0.0; 3])).is_none());
        let moving: Vec<_> = (0..2000)
            .map(|i| sample(i as f64 * 0.002, [0.0, 0.0, G], [(i % 2) as f64, 0.0, 0.0]))
            .collect();
        assert!(find_stationary(&moving).is_none());
    }

    fn forward_only() -> Params {
        Params {
            two_pass: false,
            ..Params::default()
        }
    }

    #[test]
    fn saturation_lowers_confidence_where_both_passes_are_near_it() {
        let mut s = level(1000, [0.0; 3]);
        s[200].saturated = true;
        // Two-pass: the backward pass is clean after a lone disturbance, so only the sample itself is low
        let t = run(&s, [0.0; 3], [0.0, 0.0, G], Params::default());
        assert_eq!((t.conf[199], t.conf[200], t.conf[201]), (1.0, 0.5, 1.0));
        // Between two disturbances 0.4 s apart there is no clean side: low throughout
        s[300].saturated = true;
        let t = run(&s, [0.0; 3], [0.0, 0.0, G], Params::default());
        assert!(t.conf[200..=300].iter().all(|&c| c == 0.5));
        assert_eq!(t.conf[320], 1.0);
        // Forward only: the sample and the following 0.5 s
        let mut s = level(1000, [0.0; 3]);
        s[200].saturated = true;
        let t = run(&s, [0.0; 3], [0.0, 0.0, G], forward_only());
        assert_eq!(
            (
                t.conf[199],
                t.conf[200],
                t.conf[200 + 249],
                t.conf[200 + 260]
            ),
            (1.0, 0.5, 0.5, 1.0)
        );
        assert_eq!(t.at(0.4005).unwrap().conf, 0.5);
    }

    #[test]
    fn short_gaps_are_integrated_across() {
        // Yaw rate 1500 dps * sin^2(2 pi 5 t) (a 100 ms swing-like pulse train) for 0.5 s; a 14-packet hole on a ramp
        let rate = |t: f64| 1500f64.to_radians() * (std::f64::consts::TAU * 5.0 * t).sin().powi(2);
        let full: Vec<_> = (0..251)
            .map(|i| {
                sample(
                    i as f64 * 0.002,
                    [0.0, 0.0, G],
                    [0.0, 0.0, rate(i as f64 * 0.002)],
                )
            })
            .collect();
        let holey: Vec<_> = full
            .iter()
            .enumerate()
            .filter(|(i, _)| !(60..74).contains(i))
            .map(|(_, s)| *s)
            .collect();
        let end = |s: &[Sample]| {
            *run(s, [0.0; 3], [0.0, 0.0, G], forward_only())
                .q
                .last()
                .unwrap()
        };
        let err = angle_between(end(&full), end(&holey)).to_degrees();
        assert!(err < 0.5, "{err}");
        let t = run(&holey, [0.0; 3], [0.0, 0.0, G], forward_only());
        assert_eq!(t.conf[59], 1.0);
        assert_eq!(t.conf[60], 0.5, "a hole of 4+ packets lowers confidence");
    }

    #[test]
    fn lost_packets_reinit_tilt_and_keep_heading() {
        let w = 90f64.to_radians();
        let mut s = level(251, [0.0, 0.0, w]);
        // 100 ms hole, then the sensor is tilted 30 degrees (rolled about x)
        let tilt = from_axis_angle([1.0, 0.0, 0.0], 30f64.to_radians());
        let a = rotate(crate::imu::conj(tilt), [0.0, 0.0, G]);
        for i in 0..250 {
            s.push(sample(0.5 + 0.1 + i as f64 * 0.002, a, [0.0; 3]));
        }
        let t = run(&s, [0.0; 3], [0.0, 0.0, G], forward_only());
        let i = 251;
        assert_eq!(t.conf[i], 0.5);
        // Tilt snapped to gravity immediately: sensor "up" in court frame is vertical
        let up = rotate(t.q[i], a);
        assert!((up[1] / G - 1.0).abs() < 1e-6, "{up:?}");
        // The gap itself is not interpolated
        assert!(t.at(0.55).is_none());
    }

    #[test]
    fn backward_pass_removes_the_error_a_hole_leaves_behind() {
        // Roll about the sensor x axis at 20 dps with a 1000 dps burst in 2.000-2.044 s, exactly the 22 packets that are lost.
        // Accelerometer = gravity in the rolled sensor frame (valid throughout).
        let burst = |t: f64| (2.0..2.044).contains(&t);
        let rate = |t: f64| (if burst(t) { 1000.0f64 } else { 20.0 }).to_radians();
        let mut theta = 0.0f64;
        let mut full = Vec::new();
        for i in 0..3000 {
            let t = i as f64 * 0.002;
            theta += rate(t) * 0.002;
            full.push(sample(
                t,
                [0.0, G * theta.sin(), G * theta.cos()],
                [rate(t), 0.0, 0.0],
            ));
        }
        let keep: Vec<usize> = (0..3000).filter(|&i| !burst(i as f64 * 0.002)).collect();
        assert_eq!(3000 - keep.len(), 22);
        let holey: Vec<_> = keep.iter().map(|&i| full[i]).collect();
        let reference = run(&full, [0.0; 3], [0.0, 0.0, G], forward_only());
        let err_at = |p: Params, t: f64| {
            let t_out = run(&holey, [0.0; 3], [0.0, 0.0, G], p);
            let j = keep.iter().position(|&i| i as f64 * 0.002 >= t).unwrap();
            angle_between(t_out.q[j], reference.q[keep[j]]).to_degrees()
        };
        let fwd = err_at(forward_only(), 2.8);
        let both = err_at(Params::default(), 2.8);
        assert!(fwd > 20.0, "forward alone keeps the lost 44 degrees: {fwd}");
        assert!(both < 3.0, "forward-backward: {both}");
    }
}
