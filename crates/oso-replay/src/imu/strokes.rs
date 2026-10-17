//! Stroke events: shuttle contacts (every player) + IMU swings (one player) + rule-based labels from the outgoing flight.
//! The labels are explicit hand-written rules (below), not a trained classifier.

use super::swings::Swing;
use oso_render::replay::{EVENT_CONTACT, Replay, STROKE_FIELDS};

pub use oso_render::replay::STROKE_TYPES;

/// A contact within this long of a swing window (either side) belongs to that swing
pub const MATCH_WINDOW_S: f64 = 0.06;
/// Contact-only strokes get placeholder t_start/t_end this far from the contact
pub const PLACEHOLDER_S: f64 = 0.15;

// ponytail: the thresholds below are hand-picked for the synthetic flights; the trained classifier replaces them. Tune on
// real footage using the rule inputs `oso-imu` prints per stroke. Heights/distances in m, speeds in m/s, angles in degrees.
const SERVE_QUIET_S: f64 = 3.0;
const SERVE_MAX_H: f64 = 1.15;
const SMASH_MIN_H: f64 = 2.0;
const SMASH_MAX_ANGLE: f64 = -5.0;
const SMASH_MIN_V0: f64 = 25.0;
const NET_MAX_X_CONTACT: f64 = 2.2;
const NET_MAX_APEX: f64 = 2.3;
const NET_MAX_X_END: f64 = 2.5;
const DROP_MIN_H: f64 = 1.8;
const DROP_MAX_X_END: f64 = 3.2;
const DROP_MAX_V0: f64 = 20.0;
const CLEAR_MIN_H: f64 = 1.8;
const CLEAR_MIN_APEX: f64 = 4.5;
const CLEAR_MIN_X_END: f64 = 4.5;
const LIFT_MAX_H: f64 = 1.2;
const LIFT_MIN_APEX: f64 = 3.5;
const DRIVE_MIN_H: f64 = 0.6;
const DRIVE_MAX_H: f64 = 1.9;
const DRIVE_APEX_ABOVE_H: f64 = 0.6;
const DRIVE_MIN_V0: f64 = 12.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Unknown = 0,
    Serve,
    Clear,
    Smash,
    Drop,
    Drive,
    Lift,
    Net,
    SwingOnly,
    // Only the trained classifier (tools/classify_strokes.py) emits these; the rules never do
    Block,
    Push,
    Press,
    NetKill,
    FlickServe,
}

/// What the rules look at. NaN where unknown (no outgoing flight): every comparison then fails.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RuleInputs {
    /// No contact in the 3 s before this one
    pub first_of_rally: bool,
    /// Contact height and |x| (distance from the net plane)
    pub h: f64,
    pub x_contact: f64,
    /// Launch speed and elevation (+ up)
    pub v0: f64,
    pub angle_deg: f64,
    pub apex: f64,
    /// |x| where the outgoing flight ends (next contact or landing)
    pub x_end: f64,
}

/// First match wins.
pub fn classify(i: &RuleInputs) -> Kind {
    let (h, v0, ang, apex) = (i.h, i.v0, i.angle_deg, i.apex);
    if i.first_of_rally && h < SERVE_MAX_H {
        Kind::Serve
    } else if h > SMASH_MIN_H && ang < SMASH_MAX_ANGLE && v0 > SMASH_MIN_V0 {
        Kind::Smash
    } else if i.x_contact < NET_MAX_X_CONTACT && apex < NET_MAX_APEX && i.x_end < NET_MAX_X_END {
        Kind::Net
    } else if h > DROP_MIN_H && i.x_end < DROP_MAX_X_END && v0 < DROP_MAX_V0 {
        Kind::Drop
    } else if h > CLEAR_MIN_H && apex > CLEAR_MIN_APEX && i.x_end > CLEAR_MIN_X_END {
        Kind::Clear
    } else if h < LIFT_MAX_H && apex > LIFT_MIN_APEX {
        Kind::Lift
    } else if h > DRIVE_MIN_H
        && h < DRIVE_MAX_H
        && apex < h + DRIVE_APEX_ABOVE_H
        && v0 > DRIVE_MIN_V0
    {
        Kind::Drive
    } else {
        Kind::Unknown
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Stroke {
    pub player: usize,
    pub kind: Kind,
    pub t_contact: Option<f64>,
    pub swing: Option<Swing>,
    /// m/s, max of the racquet track's head speed over the swing window
    pub head_speed_peak: Option<f64>,
    /// Rule inputs; None for air swings
    pub rules: Option<RuleInputs>,
}

impl Stroke {
    /// Time the list is sorted by and the viewer jumps to (minus lead-in)
    pub fn t_key(&self) -> f64 {
        self.swing
            .as_ref()
            .map(|s| s.t_peak)
            .or(self.t_contact)
            .unwrap_or(0.0)
    }

    /// One `swings` track row (NaN where not applicable; see docs/data-format.md)
    pub fn row(&self) -> [f32; STROKE_FIELDS] {
        let nan = f64::NAN;
        let tc = self.t_contact.unwrap_or(nan);
        let (t0, tp, t1) = match &self.swing {
            Some(s) => (s.t_start, s.t_peak, s.t_end),
            None => (tc - PLACEHOLDER_S, tc, tc + PLACEHOLDER_S),
        };
        let sw = self.swing.as_ref();
        let r = self.rules.as_ref();
        [
            t0,
            tp,
            t1,
            tc,
            self.player as f64,
            self.kind as u8 as f64,
            sw.map_or(nan, |s| s.peak_dps),
            self.head_speed_peak.unwrap_or(nan),
            sw.map_or(nan, Swing::head_speed_rot),
            sw.and_then(|s| s.impact).map_or(nan, |i| i.1),
            sw.and_then(Swing::timing_ms).unwrap_or(nan),
            r.map_or(nan, |r| r.h),
            r.map_or(nan, |r| r.v0),
            r.map_or(nan, |r| r.angle_deg),
            sw.map_or(0.0, |s| s.saturated as u8 as f64),
            sw.map_or(0.0, |s| s.confidence as f64),
        ]
        .map(|v| v as f32)
    }
}

/// The flight leaving the contact at `ev` (index into `replay.events()`): launch speed, elevation, apex, and where it ends.
fn outgoing(replay: &Replay, ev: usize) -> Option<(f64, f64, f64, f64)> {
    let events = replay.events();
    let fps = replay.fps as f64;
    let t_c = events[ev][0] as f64;
    let next = events[ev + 1..].iter().find(|e| e[0] as f64 > t_c + 1e-6);
    let mut f = (t_c * fps).ceil() as usize;
    let first = loop {
        let r = replay.shuttle(f)?;
        if r[6] > 0.0 {
            break *r;
        }
        f += 1;
        // A flight starts within a few frames of its contact
        if f as f64 / fps > t_c + 0.1 || f >= replay.frame_count {
            return None;
        }
    };
    let (mut apex, mut last_x) = (f64::MIN, 0.0);
    while f < replay.frame_count {
        let r = replay.shuttle(f)?;
        if r[6] <= 0.0 || r[7] != first[7] {
            break;
        }
        apex = apex.max(r[1] as f64);
        last_x = r[0] as f64;
        f += 1;
    }
    let (vx, vy, vz) = (first[3] as f64, first[4] as f64, first[5] as f64);
    let v0 = (vx * vx + vy * vy + vz * vz).sqrt();
    let angle = vy.atan2(vx.hypot(vz)).to_degrees();
    let x_end = next.map_or(last_x, |e| e[3] as f64);
    Some((v0, angle, apex, x_end.abs()))
}

/// `head_speed`: per video frame for the IMU player (NaN where unknown); `imu_player` None = no swings.
pub fn build(
    swings: &[Swing],
    imu_player: usize,
    head_speed: &[f32],
    replay: &Replay,
) -> Vec<Stroke> {
    let events = replay.events();
    let fps = replay.fps as f64;
    let mut out = Vec::new();
    let mut claimed: Vec<Option<usize>> = vec![None; events.len()];
    let contacts: Vec<usize> = (0..events.len())
        .filter(|&i| events[i][1] == EVENT_CONTACT)
        .collect();
    // Each swing takes the closest unclaimed contact of the IMU player inside its window +- 60 ms
    for (si, s) in swings.iter().enumerate() {
        let best = contacts
            .iter()
            .copied()
            .filter(|&i| events[i][2] == imu_player as f32 && claimed[i].is_none())
            .filter(|&i| {
                let t = events[i][0] as f64;
                t >= s.t_start - MATCH_WINDOW_S && t <= s.t_end + MATCH_WINDOW_S
            })
            .min_by(|&x, &y| {
                let d = |i: usize| (events[i][0] as f64 - s.t_peak).abs();
                d(x).total_cmp(&d(y))
            });
        match best {
            Some(i) => claimed[i] = Some(si),
            None => out.push(Stroke {
                player: imu_player,
                kind: Kind::SwingOnly,
                t_contact: None,
                swing: Some(s.clone()),
                head_speed_peak: peak_speed(head_speed, fps, s),
                rules: None,
            }),
        }
    }
    for &i in &contacts {
        let e = &events[i];
        let t = e[0] as f64;
        let first_of_rally = !contacts
            .iter()
            .any(|&j| events[j][0] as f64 >= t - SERVE_QUIET_S && events[j][0] as f64 + 1e-6 < t);
        let (v0, angle_deg, apex, x_end) =
            outgoing(replay, i).unwrap_or((f64::NAN, f64::NAN, f64::NAN, f64::NAN));
        let rules = RuleInputs {
            first_of_rally,
            h: e[4] as f64,
            x_contact: (e[3] as f64).abs(),
            v0,
            angle_deg,
            apex,
            x_end,
        };
        let swing = claimed[i].map(|si| swings[si].clone());
        out.push(Stroke {
            player: e[2].max(0.0) as usize,
            kind: classify(&rules),
            t_contact: Some(t),
            head_speed_peak: swing.as_ref().and_then(|s| peak_speed(head_speed, fps, s)),
            swing,
            rules: Some(rules),
        });
    }
    out.sort_by(|a, b| a.t_key().total_cmp(&b.t_key()));
    out
}

fn peak_speed(head_speed: &[f32], fps: f64, s: &Swing) -> Option<f64> {
    let lo = (s.t_start * fps).floor().max(0.0) as usize;
    let hi = ((s.t_end * fps).ceil().max(0.0) as usize).min(head_speed.len().saturating_sub(1));
    head_speed
        .get(lo..=hi)?
        .iter()
        .filter(|v| v.is_finite())
        .map(|&v| v as f64)
        .max_by(f64::total_cmp)
}

#[cfg(test)]
mod tests {
    use super::*;
    use oso_render::replay::{next_time, prev_time};

    #[test]
    fn kind_matches_names() {
        assert_eq!(STROKE_TYPES[Kind::SwingOnly as usize], "swing_only");
        assert_eq!(STROKE_TYPES[Kind::FlickServe as usize], "flick_serve");
        assert_eq!(STROKE_TYPES.len(), Kind::FlickServe as usize + 1);
    }

    fn inp(h: f64, v0: f64, ang: f64, apex: f64, x_c: f64, x_end: f64) -> RuleInputs {
        RuleInputs {
            first_of_rally: false,
            h,
            x_contact: x_c,
            v0,
            angle_deg: ang,
            apex,
            x_end,
        }
    }

    #[test]
    fn each_rule_has_a_case() {
        let mut serve = inp(0.9, 14.0, 30.0, 3.0, 5.0, 4.0);
        serve.first_of_rally = true;
        assert_eq!(classify(&serve), Kind::Serve);
        assert_eq!(classify(&inp(2.6, 55.0, -20.0, 2.6, 3.0, 4.0)), Kind::Smash);
        assert_eq!(classify(&inp(1.0, 5.0, 40.0, 1.9, 1.0, 1.2)), Kind::Net);
        assert_eq!(classify(&inp(2.4, 14.0, 5.0, 2.8, 3.5, 2.0)), Kind::Drop);
        assert_eq!(classify(&inp(2.4, 28.0, 40.0, 7.0, 3.5, 5.5)), Kind::Clear);
        assert_eq!(classify(&inp(0.6, 18.0, 55.0, 5.0, 2.5, 5.5)), Kind::Lift);
        assert_eq!(classify(&inp(1.3, 25.0, 3.0, 1.6, 3.5, 5.0)), Kind::Drive);
        assert_eq!(classify(&inp(1.5, 5.0, 3.0, 4.0, 3.5, 5.0)), Kind::Unknown);
        // No outgoing flight: nothing but a serve can match
        let nan = f64::NAN;
        assert_eq!(classify(&inp(2.0, nan, nan, nan, 3.0, nan)), Kind::Unknown);
    }

    #[test]
    fn first_match_wins() {
        // Qualifies as a serve and as a lift: serve is first
        let mut s = inp(0.8, 18.0, 55.0, 5.0, 2.5, 5.5);
        s.first_of_rally = true;
        assert_eq!(classify(&s), Kind::Serve);
        s.first_of_rally = false;
        assert_eq!(classify(&s), Kind::Lift);
        // Smash conditions that also satisfy the drop distance rule: smash first
        assert_eq!(classify(&inp(2.5, 30.0, -10.0, 2.5, 3.0, 3.0)), Kind::Smash);
        // Net before drop: a soft shot from near the net that also meets the drop numbers
        assert_eq!(classify(&inp(1.9, 10.0, 0.0, 2.0, 1.5, 2.0)), Kind::Net);
    }

    #[test]
    fn seek_logic_at_the_boundaries() {
        let ts = [1.0, 2.0, 3.0];
        assert_eq!(next_time(&ts, 0.0), Some(1.0));
        assert_eq!(prev_time(&ts, 0.0), None);
        assert_eq!(next_time(&ts, 1.0), Some(2.0));
        assert_eq!(prev_time(&ts, 1.0), None);
        assert_eq!(next_time(&ts, 2.0), Some(3.0));
        assert_eq!(prev_time(&ts, 2.0), Some(1.0));
        assert_eq!(next_time(&ts, 3.0), None);
        assert_eq!(prev_time(&ts, 3.0), Some(2.0));
        assert_eq!(next_time(&ts, 9.0), None);
        assert_eq!(prev_time(&ts, 9.0), Some(3.0));
        assert_eq!(next_time(&[], 1.0), None);
    }

    fn swing(t0: f64, tp: f64, t1: f64) -> Swing {
        Swing {
            t_start: t0,
            t_peak: tp,
            t_end: t1,
            t_forward: t0,
            t_follow: tp,
            peak_dps: 900.0,
            impact: Some((tp + 0.004, 12.0)),
            saturated: false,
            confidence: 1.0,
        }
    }

    /// 2 s replay at 60 fps with a lob from player 0 at t = 0.5 (apex 6 m, lands 6 m away) and a smash back at 1.5
    fn rally() -> Replay {
        let fps = 60;
        let n = 120;
        let mut sh = vec![[0.0f32; 8]; n];
        // Flight 0: frames 31..90 (t 0.5167 .. 1.5): parabola with apex 6 m at t = 1.0
        for (f, row) in sh.iter_mut().enumerate().take(90).skip(31) {
            let t = f as f32 / 60.0;
            *row = [
                -4.0 + 8.0 * (t - 0.5),
                1.0 + 5.0 * (1.0 - ((t - 1.0) / 0.5).powi(2)),
                0.0,
                8.0,
                -20.0 * (t - 1.0),
                0.0,
                1.0,
                0.0,
            ];
        }
        // Flight 1: after the smash at 1.5, fast and downward
        for (f, row) in sh.iter_mut().enumerate().take(110).skip(91) {
            let t = f as f32 / 60.0;
            *row = [
                4.0 - 20.0 * (t - 1.5),
                2.5 - 20.0 * (t - 1.5),
                0.0,
                -30.0,
                -25.0,
                0.0,
                1.0,
                1.0,
            ];
        }
        let events = vec![
            [0.5, 1.0, 0.0, -4.0, 1.0, 0.0],
            [1.5, 1.0, 1.0, 4.0, 2.5, 0.0],
            [1.62, 2.0, -1.0, 2.0, 0.0, 0.0],
        ];
        Replay::from_parts(
            fps as f32,
            n,
            2,
            vec![[0.0; 4]; n * 2 * oso_render::replay::JOINTS],
            None,
        )
        .with_shuttle(sh, events)
    }

    #[test]
    fn contacts_become_strokes_and_swings_attach_only_inside_the_window() {
        let r = rally();
        let hs = vec![f32::NAN; 120];
        // Swing around the first contact (0.5); a second swing nowhere near a contact; none for the smash (player 1)
        let swings = [swing(0.42, 0.5, 0.58), swing(1.0, 1.05, 1.12)];
        let s = build(&swings, 0, &hs, &r);
        assert_eq!(s.len(), 3);
        assert_eq!((s[0].kind, s[0].swing.is_some()), (Kind::Serve, true));
        assert_eq!((s[1].kind, s[1].t_contact), (Kind::SwingOnly, None));
        assert_eq!(
            (s[2].kind, s[2].player, s[2].swing.is_some()),
            (Kind::Smash, 1, false)
        );
        // Sorted by time, no duplicates for the same player
        assert!(s.windows(2).all(|w| w[0].t_key() <= w[1].t_key()));
        let row = s[2].row();
        assert!(row[6].is_nan() && row[9].is_nan() && (row[3] - 1.5).abs() < 1e-6);
        assert!((row[0] - 1.35).abs() < 1e-5 && (row[2] - 1.65).abs() < 1e-5);
        assert_eq!(row[5], Kind::Smash as u8 as f32);
        let air = s[1].row();
        assert!(air[3].is_nan() && air[11].is_nan() && air[6] == 900.0 && air[5] == 8.0);
        // A swing 0.1 s from the contact (> 60 ms outside its window) does not claim it
        let far = build(&[swing(0.2, 0.25, 0.34)], 0, &hs, &r);
        assert!(
            far.iter().any(|x| x.kind == Kind::SwingOnly)
                && far.iter().any(|x| x.swing.is_none() && x.player == 0)
        );
        // Without any IMU swings contacts still appear with flight-based fields
        assert_eq!(build(&[], 0, &hs, &r).len(), 2);
    }

    #[test]
    fn outgoing_flight_inputs() {
        let r = rally();
        let (v0, ang, apex, x_end) = outgoing(&r, 0).unwrap();
        assert!(
            v0 > 8.0 && ang > 30.0 && (apex - 6.0).abs() < 0.1,
            "{v0} {ang} {apex}"
        );
        assert_eq!(x_end, 4.0);
        let (_, ang1, _, x1) = outgoing(&r, 1).unwrap();
        assert!(ang1 < -30.0 && x1 == 2.0);
    }

    #[test]
    fn head_speed_peak_over_the_window() {
        let mut hs = vec![1.0f32; 120];
        hs[40] = 30.0;
        hs[100] = 99.0;
        let s = swing(0.5, 0.65, 0.8);
        assert_eq!(peak_speed(&hs, 60.0, &s), Some(30.0));
        assert_eq!(peak_speed(&vec![f32::NAN; 120], 60.0, &s), None);
    }
}
