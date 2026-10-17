//! Video yaw correction. The accelerometer fixes tilt, so only yaw (rotation about court +Y) is unknown: arbitrary at
//! start, drifting afterwards. Each frame compares the heading of the handle axis (from the IMU) with the heading of the
//! forearm (from video), both projected on the floor; the difference is low-passed and applied as a yaw-only rotation.
//!
//! ponytail: forearm heading is only a proxy for racquet heading (wrist deviation); fine for drift and the constant
//! alignment, wrong for fast wrist yaw, which the IMU carries. Tilt mount errors (~3 deg) are ignored.

use super::{Quat, Vec3, rotate};

/// Low-pass time constant, seconds
pub const TAU_S: f64 = 4.0;
/// Frames where either axis is within ~72 degrees of vertical have no usable heading
const MIN_HORIZONTAL: f64 = 0.3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum YawMode {
    /// Constant alignment plus the 4 s low-passed drift tracking
    Full,
    /// Constant alignment only (initial heading + mount), no drift tracking
    ConstOnly,
    /// IMU heading as fused (arbitrary start): `--no-video-yaw`
    None,
}

#[derive(Debug, Clone)]
pub struct Yaw {
    /// Yaw correction per frame, radians about court +Y (applied as `yaw_quat(y) * q`)
    pub per_frame: Vec<f64>,
    /// Weighted circular mean of the heading difference over the clip: initial heading + yaw mount + mean wrist deviation
    pub constant: f64,
    /// Frames that carried a usable heading
    pub frames_used: usize,
}

/// Rotation about +Y that takes +X to this horizontal direction: atan2(-z, x).
pub fn heading(v: Vec3) -> f64 {
    (-v[2]).atan2(v[0])
}

fn horizontal(v: Vec3) -> f64 {
    v[0].hypot(v[2]) / super::norm(v).max(1e-12)
}

/// `q`: raw IMU orientation per frame (None outside the recording); `fore`: unit forearm direction per frame where both
/// forearm joints are conf 1.
pub fn estimate(q: &[Option<Quat>], fore: &[Option<Vec3>], fps: f64, mode: YawMode) -> Yaw {
    // (weight, heading difference video - imu) per frame
    type Obs = Option<(f64, f64)>;
    let n = q.len();
    let obs: Vec<Obs> = (0..n)
        .map(|f| {
            let (q, v) = (q[f]?, fore[f]?);
            let h = rotate(q, [0.0, 1.0, 0.0]);
            let (hv, hh) = (horizontal(v), horizontal(h));
            (hv > MIN_HORIZONTAL && hh > MIN_HORIZONTAL)
                .then(|| ((hv * hh).powi(2), heading(v) - heading(h)))
        })
        .collect();
    let (mut sx, mut sy, mut used) = (0.0, 0.0, 0);
    for &(w, d) in obs.iter().flatten() {
        sx += w * d.cos();
        sy += w * d.sin();
        used += 1;
    }
    let constant = if used > 0 { sy.atan2(sx) } else { 0.0 };
    let per_frame = match mode {
        YawMode::None => vec![0.0; n],
        YawMode::ConstOnly => vec![constant; n],
        YawMode::Full => {
            // Exponential smoother on the unit heading-difference vector, weighted by how horizontal the arm is
            let (mut cx, mut cy) = (constant.cos(), constant.sin());
            obs.iter()
                .map(|o| {
                    if let &Some((w, d)) = o {
                        let alpha = 1.0 - (-w / (fps * TAU_S)).exp();
                        cx += alpha * (d.cos() - cx);
                        cy += alpha * (d.sin() - cy);
                    }
                    cy.atan2(cx)
                })
                .collect()
        }
    };
    Yaw {
        per_frame,
        constant,
        frames_used: used,
    }
}

/// Smallest signed angle a - b, radians.
pub fn angle_diff(a: f64, b: f64) -> f64 {
    let d = (a - b) % std::f64::consts::TAU;
    if d > std::f64::consts::PI {
        d - std::f64::consts::TAU
    } else if d < -std::f64::consts::PI {
        d + std::f64::consts::TAU
    } else {
        d
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::imu::{from_two_vectors, mul, yaw_quat};

    // Forearm sweeping back and forth horizontally; IMU heading wrong by 30 deg + 1 deg/s drift
    fn scene(fps: f64, secs: f64) -> (Vec<Option<Quat>>, Vec<Option<Vec3>>, Vec<f64>) {
        let n = (fps * secs) as usize;
        let (mut q, mut fore, mut err) = (vec![], vec![], vec![]);
        for f in 0..n {
            let t = f as f64 / fps;
            let psi = 0.4 * (t * 1.3).sin() + 0.5;
            let dir = [psi.cos(), 0.2, -psi.sin()];
            let q_true = from_two_vectors([0.0, 1.0, 0.0], dir);
            let e = 30f64.to_radians() + 1f64.to_radians() * t;
            // IMU yaw error e: its handle heading is the true one minus e
            q.push(Some(mul(yaw_quat(-e), q_true)));
            fore.push(Some(dir));
            err.push(e);
        }
        (q, fore, err)
    }

    #[test]
    fn recovers_constant_and_tracks_drift() {
        let fps = 60.0;
        let (q, fore, err) = scene(fps, 30.0);
        let full = estimate(&q, &fore, fps, YawMode::Full);
        let konst = estimate(&q, &fore, fps, YawMode::ConstOnly);
        let none = estimate(&q, &fore, fps, YawMode::None);
        assert_eq!(full.frames_used, q.len());
        // Constant = mean error over the clip (30 + 15 deg)
        assert!(
            (konst.constant.to_degrees() - 45.0).abs() < 0.5,
            "{}",
            konst.constant.to_degrees()
        );
        let end = q.len() - 1;
        let resid = |y: &Yaw| angle_diff(err[end], y.per_frame[end]).abs().to_degrees();
        // The low-pass lags a ramp by about 1 deg/s * 4 s, far better than constant-only or nothing
        assert!(resid(&full) < 6.0, "{}", resid(&full));
        assert!(resid(&full) < resid(&konst) && resid(&konst) < resid(&none));
        assert_eq!(none.per_frame[end], 0.0);
    }

    #[test]
    fn vertical_arm_and_missing_frames_are_skipped() {
        let (mut q, mut fore, _) = scene(60.0, 2.0);
        for x in fore.iter_mut().take(60) {
            *x = Some([0.01, -1.0, 0.0]); // hanging straight down: heading undefined
        }
        q[100] = None;
        fore[101] = None;
        let y = estimate(&q, &fore, 60.0, YawMode::Full);
        assert_eq!(y.frames_used, q.len() - 60 - 2);
    }

    #[test]
    fn angle_wrap() {
        assert!((angle_diff(3.0, -3.0) - (6.0 - std::f64::consts::TAU)).abs() < 1e-12);
        assert!((angle_diff(0.1, 0.3) + 0.2).abs() < 1e-12);
    }
}
