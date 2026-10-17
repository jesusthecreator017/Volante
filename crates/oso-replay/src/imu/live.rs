//! Incremental orientation for the live view (`oso-rec live`): the offline forward Madgwick pass (same filter, gains
//! and lost-packet handling as `fusion::run` with `two_pass: false`), fed packet by packet.
//!
//! The Hermite fill over a hole needs the sample after it, so the newest sample is held back: the output lags the
//! stream by one sample (2 ms at 500 Hz). The first second is spent calibrating (gyro bias and starting tilt from
//! the pod being held still); until then the output is the identity.

use super::fusion::{
    self, DEFAULT_BETA, Params, from_madgwick, mean_acc, step, to_madgwick, world_to_court,
};
use super::packet::{self, Sample};
use super::mount::{Mount, POSE_SAMPLES};
use super::{IDENTITY, Quat, Vec3, from_two_vectors, mul, normalize, rotate, yaw_quat};
use oso_proto::{PACKET_LEN, Packet};
use std::collections::VecDeque;

/// Samples held for calibration (1 s at 500 Hz: `find_stationary` needs ten 50-sample blocks)
const WARMUP: usize = 500;
const START_TILT: usize = 50;
/// Heading source switch, degrees of the shaft from vertical: within `ENTER_NORMAL_DEG` the face normal is used, until
/// the shaft is more than `LEAVE_NORMAL_DEG` away again (hysteresis, so R does not flip between sources near the edge)
const ENTER_NORMAL_DEG: f64 = 20.0;
const LEAVE_NORMAL_DEG: f64 = 35.0;

pub struct LiveFusion {
    params: Params,
    bias: Vec3,
    /// Madgwick-frame (w, x, y, z)
    q: [f64; 4],
    /// Samples before the newest one: `prev2`, `prev`, `pending` (the newest, not yet integrated)
    prev2: Option<Sample>,
    prev: Option<Sample>,
    pending: Option<Sample>,
    /// Some while calibrating
    warm: Option<Vec<Sample>>,
    /// Previous arrival (seq16, seq, t32, t_us), for unwrapping like `packet::parse`
    last: Option<(u16, i64, u32, i64)>,
    top_seq: i64,
    yaw: Quat,
    mount: Mount,
    /// The last second of raw (sensor-frame) samples, for the mount calibration
    raw: VecDeque<Sample>,
    /// Heading currently taken from the face normal (see `heading_with`)
    use_normal: bool,
    pub received: u64,
    pub lost: u64,
}

impl Default for LiveFusion {
    fn default() -> Self {
        Self::new()
    }
}

impl LiveFusion {
    /// Calibrates from the first second of samples
    pub fn new() -> Self {
        let mut f = Self::with_calibration([0.0; 3], [0.0, 0.0, 1.0]);
        f.warm = Some(Vec::with_capacity(WARMUP));
        f
    }

    /// Starts at once from a known gyro bias and rest accelerometer direction (tests, replays of a calibrated clip)
    pub fn with_calibration(bias: Vec3, init_acc: Vec3) -> Self {
        let up = [0.0, 0.0, 1.0];
        Self {
            params: Params {
                two_pass: false,
                beta: DEFAULT_BETA,
                ..Params::default()
            },
            bias,
            q: to_madgwick(from_two_vectors(init_acc, up)),
            prev2: None,
            prev: None,
            pending: None,
            warm: None,
            last: None,
            top_seq: i64::MIN,
            yaw: IDENTITY,
            mount: Mount::IDENTITY,
            raw: VecDeque::new(),
            use_normal: false,
            received: 0,
            lost: 0,
        }
    }

    /// Like `new`, with the IMU mounted as `mount` (applied to every sample before fusion)
    pub fn with_mount(mount: Mount) -> Self {
        Self { mount, ..Self::new() }
    }

    /// The last second of raw sensor-frame samples (before the mount)
    pub fn recent_raw(&self) -> Vec<Sample> {
        self.raw.iter().copied().collect()
    }

    pub fn calibrating(&self) -> bool {
        self.warm.is_some()
    }

    /// One raw stream packet. Duplicates and late arrivals are dropped; missing seqs count as lost.
    pub fn push_packet(&mut self, raw: &[u8; PACKET_LEN]) {
        let Packet {
            seq: s16,
            t_us: t32,
            v,
        } = Packet::decode(raw);
        let (seq, t_us) = match self.last {
            None => (s16 as i64, t32 as i64),
            Some((ps, pseq, pt, ptu)) => (
                pseq + s16.wrapping_sub(ps) as i16 as i64,
                ptu + t32.wrapping_sub(pt) as i32 as i64,
            ),
        };
        self.last = Some((s16, seq, t32, t_us));
        if seq <= self.top_seq {
            return;
        }
        if self.top_seq != i64::MIN {
            self.lost += (seq - self.top_seq - 1) as u64;
        }
        self.top_seq = seq;
        self.received += 1;
        self.push_sample(packet::to_sample(t_us, v));
    }

    /// A sample in time order
    pub fn push_sample(&mut self, raw: Sample) {
        if self.raw.len() == POSE_SAMPLES {
            self.raw.pop_front();
        }
        self.raw.push_back(raw);
        let s = self.mount.sample(&raw);
        match &mut self.warm {
            Some(w) => {
                w.push(s);
                if w.len() >= WARMUP {
                    self.finish_calibration();
                }
            }
            None => self.advance(s),
        }
    }

    fn finish_calibration(&mut self) {
        let Some(w) = self.warm.take() else { return };
        if let Some(win) = fusion::find_stationary(&w, 1.0 / self.params.nominal_dt) {
            self.bias = fusion::gyro_bias(&w, win);
        }
        let acc = mean_acc(&w[..START_TILT]);
        let acc = if super::norm(acc) > 1e-6 {
            acc
        } else {
            [0.0, 0.0, 1.0]
        };
        self.q = to_madgwick(from_two_vectors(acc, [0.0, 0.0, 1.0]));
        for s in w {
            self.advance(s);
        }
    }

    fn advance(&mut self, s: Sample) {
        if let Some(p) = self.pending {
            step(
                &mut self.q,
                self.prev2.as_ref(),
                self.prev.as_ref(),
                &p,
                Some(&s),
                self.bias,
                1.0,
                self.params,
            );
            self.prev2 = self.prev;
            self.prev = Some(p);
        }
        self.pending = Some(s);
    }

    /// Sensor -> court orientation with the arbitrary gyro heading, before re-centring (what `fusion::run` outputs)
    pub fn raw_orientation(&self) -> Quat {
        if self.calibrating() {
            return IDENTITY;
        }
        normalize(mul(world_to_court(), from_madgwick(self.q)))
    }

    /// Sensor -> court orientation, re-centred
    pub fn orientation(&self) -> Quat {
        normalize(mul(self.yaw, self.raw_orientation()))
    }

    /// R: the current heading becomes "forward" (+X); tilt (from gravity) is untouched
    pub fn recenter(&mut self) {
        let q = self.raw_orientation();
        self.use_normal = next_source(q, self.use_normal);
        self.yaw = yaw_quat(-heading_with(q, self.use_normal));
    }
}

/// Whether the heading should come from the face normal: the shaft is within `ENTER_NORMAL_DEG` of vertical (or was, and
/// is still within `LEAVE_NORMAL_DEG`)
fn next_source(q: Quat, was_normal: bool) -> bool {
    let off_vertical = rotate(q, [0.0, 1.0, 0.0])[1].abs().clamp(0.0, 1.0).acos().to_degrees();
    off_vertical < if was_normal { LEAVE_NORMAL_DEG } else { ENTER_NORMAL_DEG }
}

/// Heading of the racquet about the court's vertical, radians, counter-clockwise from +X seen from above (the sense of
/// `yaw_quat`). From the shaft's horizontal direction (racquet +Y) normally. With the shaft near vertical that direction
/// is meaningless, so the face normal (+Z) is used instead, turned by +90 degrees: for a shaft tipping about the normal
/// axis the two agree, so the switch does not jump (about the face's own X axis it can still differ by 90 degrees,
/// an inherent ambiguity of an upright racquet).
pub fn heading_with(q: Quat, from_normal: bool) -> f64 {
    let angle = |v: Vec3| (-v[2]).atan2(v[0]);
    if from_normal {
        angle(rotate(q, [0.0, 0.0, 1.0])) + std::f64::consts::FRAC_PI_2
    } else {
        angle(rotate(q, [0.0, 1.0, 0.0]))
    }
}

/// Stateless heading (the source picked with the entering threshold)
pub fn heading(q: Quat) -> f64 {
    heading_with(q, next_source(q, false))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::imu::angle_between;

    /// Smooth made-up motion as raw packets, seq starting near the u16 wrap
    fn stream(n: usize) -> Vec<[u8; PACKET_LEN]> {
        (0..n)
            .map(|i| {
                let t = i as f64 * 0.002;
                let g = |f: f64, a: f64| (a * (f * t).sin()) as i16;
                Packet {
                    seq: 65_000u16.wrapping_add(i as u16),
                    t_us: (t * 1e6) as u32,
                    v: [
                        g(0.3, 150.0),
                        g(0.2, 100.0),
                        2048,
                        g(0.7, 900.0),
                        g(0.5, 700.0),
                        g(0.3, 500.0),
                    ],
                }
                .encode()
            })
            .collect()
    }

    // Deterministic xorshift, no rand dependency
    fn rng(state: &mut u64) -> u64 {
        *state ^= *state << 13;
        *state ^= *state >> 7;
        *state ^= *state << 17;
        *state
    }

    fn offline(kept: &[[u8; PACKET_LEN]], bias: Vec3, acc: Vec3) -> fusion::Track {
        let mut bytes = packet::encode(500, &[]);
        bytes.extend(kept.iter().flatten());
        let samples = packet::parse(&bytes).unwrap().samples;
        fusion::run(
            &samples,
            bias,
            acc,
            Params {
                two_pass: false,
                ..Params::default()
            },
        )
    }

    #[test]
    fn matches_offline_with_random_batches_and_drops() {
        let mut rs = 0x9E37_79B9_7F4A_7C15u64;
        let mut kept = stream(6000);
        // Single drops, a 6-packet hole and a 60-packet hole (> MAX_DT)
        let drops: Vec<usize> = [
            100, 500, 501, 900, 901, 902, 903, 904, 905, 2000, 4000, 4001,
        ]
        .into_iter()
        .chain(3000..3060)
        .collect();
        for &d in drops.iter().rev() {
            kept.remove(d);
        }
        let (bias, acc) = ([0.01, -0.02, 0.005], [0.0, 0.0, 9.8]);
        let off = offline(&kept, bias, acc);
        let mut live = LiveFusion::with_calibration(bias, acc);
        let mut i = 0;
        while i < kept.len() {
            let end = (i + 1 + (rng(&mut rs) % 13) as usize).min(kept.len());
            kept[i..end].iter().for_each(|p| live.push_packet(p));
            i = end;
        }
        // Everything but the newest sample is integrated
        let err = angle_between(live.raw_orientation(), off.q[kept.len() - 2]).to_degrees();
        assert!(err < 0.5, "{err} deg");
        assert_eq!(
            (live.lost, live.received),
            (drops.len() as u64, kept.len() as u64)
        );
    }

    #[test]
    fn every_prefix_matches_offline() {
        let kept = stream(1200);
        let (bias, acc) = ([0.0; 3], [0.0, 0.0, 9.8]);
        let off = offline(&kept, bias, acc);
        let mut live = LiveFusion::with_calibration(bias, acc);
        for (i, p) in kept.iter().enumerate() {
            live.push_packet(p);
            if i >= 1 {
                assert!(
                    angle_between(live.raw_orientation(), off.q[i - 1]) < 1e-9,
                    "sample {i}"
                );
            }
        }
    }

    #[test]
    fn calibrates_then_runs_and_ignores_duplicates() {
        let mut live = LiveFusion::new();
        let s = stream(700);
        for p in &s[..499] {
            live.push_packet(p);
            live.push_packet(p);
        }
        assert!(live.calibrating() && live.raw_orientation() == IDENTITY);
        assert_eq!(live.received, 499);
        s[499..].iter().for_each(|p| live.push_packet(p));
        assert!(!live.calibrating());
        let q = live.raw_orientation();
        assert!((q.iter().map(|c| c * c).sum::<f64>() - 1.0).abs() < 1e-9);
    }

    #[test]
    fn recenter_zeroes_yaw_and_keeps_tilt() {
        let mut live = LiveFusion::with_calibration([0.0; 3], [0.0, 0.0, 9.8]);
        stream(800).iter().for_each(|p| live.push_packet(p));
        let before = live.orientation();
        assert!(
            heading(before).abs() > 0.05,
            "test needs a non-zero heading"
        );
        let (shaft, normal) = (
            rotate(before, [0.0, 1.0, 0.0]),
            rotate(before, [0.0, 0.0, 1.0]),
        );
        live.recenter();
        let after = live.orientation();
        assert!(heading(after).abs() < 1e-9, "{}", heading(after));
        let (s2, n2) = (
            rotate(after, [0.0, 1.0, 0.0]),
            rotate(after, [0.0, 0.0, 1.0]),
        );
        assert!((s2[1] - shaft[1]).abs() < 1e-9 && (n2[1] - normal[1]).abs() < 1e-9);
    }

    #[test]
    fn heading_falls_back_to_the_face_normal_with_a_vertical_shaft() {
        use crate::imu::from_axis_angle;
        // Shaft straight up (identity maps local +Y to court +Y), then turned 0.7 rad about the vertical
        for yaw in [0.7, -2.0, 0.0] {
            let q = from_axis_angle([0.0, 1.0, 0.0], yaw);
            let h = heading(q);
            assert!(h.is_finite());
            // Face normal +Z -> (sin yaw, 0, cos yaw): atan2(-cos, sin) = yaw - pi/2, plus the 90 degree turn
            let want = yaw;
            let d = (h - want).rem_euclid(std::f64::consts::TAU);
            assert!(d < 1e-9 || std::f64::consts::TAU - d < 1e-9, "{h} vs {want}");
            // After re-centring by -heading the heading is 0
            let c = mul(yaw_quat(-h), q);
            assert!(heading(c).abs() < 1e-9);
        }
    }

    #[test]
    fn heading_source_switch_is_continuous_for_a_tip_about_the_normal_and_has_hysteresis() {
        use crate::imu::from_axis_angle;
        // Shaft tipping from 60 degrees off vertical to vertical about the face normal (+Z): the shaft heading is +X
        // throughout, and the normal-based heading must agree with it
        for deg in [60.0f64, 36.0, 34.0, 19.0, 10.0, 1.0] {
            let q = from_axis_angle([0.0, 0.0, 1.0], -deg.to_radians());
            let (a, b) = (heading_with(q, false), heading_with(q, true));
            assert!(a.abs() < 1e-9 && b.abs() < 1e-9, "{deg}: {a} {b}");
        }
        let at = |deg: f64| from_axis_angle([0.0, 0.0, 1.0], -deg.to_radians());
        assert!(!next_source(at(25.0), false) && next_source(at(15.0), false));
        // Once on the normal, stays there until 35 degrees
        assert!(next_source(at(25.0), true) && next_source(at(34.0), true) && !next_source(at(36.0), true));
    }

    #[test]
    fn mount_is_applied_before_fusion_and_raw_samples_are_kept() {
        // A pod lying flat (sensor +Z up) held still: with the flat mount the racquet's shaft points up
        let still = |i: usize| Packet { seq: i as u16, t_us: i as u32 * 2000, v: [0, 0, 2048, 0, 0, 0] }.encode();
        let mut live = LiveFusion::with_mount(Mount::FLAT);
        (0..700).for_each(|i| live.push_packet(&still(i)));
        let shaft = rotate(live.raw_orientation(), [0.0, 1.0, 0.0]);
        assert!(shaft[1] > 0.999, "{shaft:?}");
        assert_eq!(live.recent_raw().len(), 500);
        assert!((live.recent_raw()[0].acc[2] as f64 - crate::imu::G).abs() < 1e-3);
        // Identity: the shaft lies flat instead
        let mut live = LiveFusion::new();
        (0..700).for_each(|i| live.push_packet(&still(i)));
        assert!(rotate(live.raw_orientation(), [0.0, 1.0, 0.0])[1].abs() < 1e-3);
    }
}
