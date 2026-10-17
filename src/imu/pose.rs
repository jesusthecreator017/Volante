//! Racquet pose per video frame: butt position from the video wrist (a model, not a measurement), orientation from
//! the synced, yaw-corrected IMU.

use super::correct::{self, Yaw, YawMode};
use super::fusion::Track;
use super::{Quat, Vec3, cross, mul, norm, rotate, yaw_quat};
use crate::replay::{RACQUET_FIELDS, Replay};

/// Butt of the handle sits this far from the wrist along the forearm. A model (the synthetic generator uses the
/// same one), not a measurement: grip changes shift the real racquet by centimeters. Mirrors tools/synth_imu.py.
pub const BUTT_OFFSET: f64 = 0.06;
/// Head center, meters from the butt along the handle. Mirrors src/scene/racquet.rs.
pub const HEAD_CENTER: f64 = 0.53;
const R_WRIST: usize = 10;
const R_ELBOW: usize = 8;

pub struct Output {
    /// `[frame][player][RACQUET_FIELDS]` flattened; players other than the IMU's are all zero (conf 0)
    pub data: Vec<f32>,
    pub yaw: Yaw,
}

fn sub(a: [f32; 4], b: [f32; 4]) -> Vec3 {
    [
        (a[0] - b[0]) as f64,
        (a[1] - b[1]) as f64,
        (a[2] - b[2]) as f64,
    ]
}

/// `sync`: `t_video = t_dev * (1 + a) + b`.
pub fn build(
    replay: &Replay,
    player: usize,
    track: &Track,
    a: f64,
    b: f64,
    mode: YawMode,
) -> Output {
    let (n, fps) = (replay.frame_count, replay.fps as f64);
    let mut q_raw: Vec<Option<Quat>> = vec![None; n];
    let mut rate: Vec<Vec3> = vec![[0.0; 3]; n];
    let mut imu_conf = vec![0.0f32; n];
    let mut fore_pos: Vec<Option<Vec3>> = vec![None; n];
    let mut fore_yaw: Vec<Option<Vec3>> = vec![None; n];
    let mut butt: Vec<Option<Vec3>> = vec![None; n];
    let mut video_conf = vec![0.0f32; n];
    for f in 0..n {
        let j = replay.joints(f, player);
        let (wr, el) = (j[R_WRIST], j[R_ELBOW]);
        if wr[3] > 0.0 && el[3] > 0.0 {
            let d = sub(wr, el);
            let len = norm(d);
            if len > 1e-6 {
                let u = d.map(|c| c / len);
                fore_pos[f] = Some(u);
                if wr[3] >= 1.0 && el[3] >= 1.0 {
                    fore_yaw[f] = Some(u);
                }
                butt[f] = Some(std::array::from_fn(|k| wr[k] as f64 + BUTT_OFFSET * u[k]));
                video_conf[f] = wr[3].min(el[3]);
            }
        }
        let t_dev = (f as f64 / fps - b) / (1.0 + a);
        if let Some(at) = track.at(t_dev) {
            q_raw[f] = Some(at.q);
            rate[f] = at.w;
            imu_conf[f] = at.conf;
        }
    }
    let yaw = correct::estimate(&q_raw, &fore_yaw, fps, mode);
    let mut data = vec![0.0f32; n * replay.players * RACQUET_FIELDS];
    for f in 0..n {
        let (Some(q), Some(p)) = (q_raw[f], butt[f]) else {
            continue;
        };
        let conf = video_conf[f].min(imu_conf[f]);
        if conf <= 0.0 {
            continue;
        }
        let q = mul(yaw_quat(yaw.per_frame[f]), q);
        // Butt velocity from neighbouring frames (one-sided at a track edge)
        let (lo, hi) = (f.saturating_sub(1), (f + 1).min(n - 1));
        let v = match (butt[lo], butt[hi]) {
            (Some(l), Some(h)) if hi > lo => {
                std::array::from_fn(|k| (h[k] - l[k]) * fps / (hi - lo) as f64)
            }
            _ => [0.0; 3],
        };
        let w_world = rotate(q, rate[f]);
        let r = rotate(q, [0.0, HEAD_CENTER, 0.0]);
        let wxr = cross(w_world, r);
        let head = norm(std::array::from_fn(|k| v[k] + wxr[k]));
        let o = (f * replay.players + player) * RACQUET_FIELDS;
        let vals = [p[0], p[1], p[2], q[0], q[1], q[2], q[3], conf as f64, head];
        for (dst, src) in data[o..o + RACQUET_FIELDS].iter_mut().zip(vals) {
            *dst = src as f32;
        }
    }
    Output { data, yaw }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::imu::{IDENTITY, angle_between, from_axis_angle};
    use crate::replay::JOINTS;

    // One player, wrist 1.2 m up moving along +x at 2 m/s, forearm along +x
    fn replay(frames: usize) -> Replay {
        let mut joints = vec![[0.0f32; 4]; frames * JOINTS];
        for f in 0..frames {
            let x = 2.0 * f as f32 / 60.0;
            joints[f * JOINTS + R_WRIST] = [x, 1.2, 0.0, 1.0];
            joints[f * JOINTS + R_ELBOW] = [x - 0.27, 1.2, 0.0, 1.0];
        }
        Replay::from_parts(60.0, frames, 1, joints, None)
    }

    #[test]
    fn places_butt_on_forearm_and_applies_sync_and_yaw() {
        let r = replay(120);
        // IMU track: device clock = video clock + 3 s, rotating about court Y at 1 rad/s, handle along +Y
        let mut track = Track::default();
        for i in 0..2000 {
            let t_dev = 3.0 + i as f64 * 0.002;
            track.t.push(t_dev);
            track
                .q
                .push(from_axis_angle([0.0, 1.0, 0.0], 0.3 * (t_dev - 3.0)));
            track.w.push([0.0, 1.0, 0.0]);
            track.conf.push(1.0);
        }
        let out = build(&r, 0, &track, 0.0, -3.0, YawMode::None);
        let e = |f: usize| &out.data[f * RACQUET_FIELDS..(f + 1) * RACQUET_FIELDS];
        // frame 30: wrist x = 1.0 -> butt x = 1.0 + 0.06
        assert!(
            (e(30)[0] - 1.06).abs() < 1e-5 && (e(30)[1] - 1.2).abs() < 1e-6,
            "{:?}",
            e(30)
        );
        assert_eq!(e(30)[7], 1.0);
        // Orientation at video t = 0.5 s is the IMU's at device 3.5 s: 0.15 rad
        let q = [
            e(30)[3] as f64,
            e(30)[4] as f64,
            e(30)[5] as f64,
            e(30)[6] as f64,
        ];
        let want = from_axis_angle([0.0, 1.0, 0.0], 0.15);
        assert!(angle_between(q, want) < 1e-4);
        // Head speed: butt moves 2 m/s along x, handle (+Y) spinning about Y adds nothing; head center on the axis
        assert!((e(30)[8] - 2.0).abs() < 1e-3, "{}", e(30)[8]);
    }

    #[test]
    fn head_speed_adds_rotation_and_missing_data_hides() {
        let r = replay(60);
        let mut track = Track::default();
        for i in 0..1000 {
            track.t.push(i as f64 * 0.002);
            // Handle along +Y, spinning about +Z at 10 rad/s: head center moves 10 * 0.53 along -X
            track.q.push(IDENTITY);
            track.w.push([0.0, 0.0, 10.0]);
            track.conf.push(if i < 300 { 1.0 } else { 0.5 });
        }
        let out = build(&r, 0, &track, 0.0, 0.0, YawMode::None);
        let e = |f: usize| &out.data[f * RACQUET_FIELDS..(f + 1) * RACQUET_FIELDS];
        // v_butt = +2 x, rotation term = -5.3 x: |v| = 3.3
        assert!((e(10)[8] - 3.3).abs() < 0.01, "{}", e(10)[8]);
        // IMU conf 0.5 after 0.6 s -> conf 0.5; beyond the recording (>2 s) none
        assert_eq!(e(50)[7], 0.5);
        let r = replay(180);
        let out = build(&r, 0, &track, 0.0, 0.0, YawMode::None);
        assert!(
            out.data[150 * RACQUET_FIELDS..151 * RACQUET_FIELDS]
                .iter()
                .all(|&v| v == 0.0)
        );
    }
}
