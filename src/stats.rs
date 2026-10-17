//! Match stats: a pure function of a [`Replay`]'s `swings` track (hits = rows with a contact). JSON shape: docs/data-format.md.

use crate::replay::{EVENT_POINT, Replay, STROKE_TYPES};
use serde::Serialize;
use std::collections::BTreeMap;

/// Contacts further apart than this start a new rally (same rule as tools/classify_strokes.py `RALLY_GAP_S`).
pub const RALLY_GAP_S: f64 = 3.0;

#[derive(Serialize, Debug, PartialEq)]
pub struct Fastest {
    pub speed: f64,
    pub t: f64,
    #[serde(rename = "type")]
    pub kind: &'static str,
}

#[derive(Serialize, Debug, PartialEq)]
pub struct Hit {
    pub t: f64,
    pub x: f64,
    pub z: f64,
    #[serde(rename = "type")]
    pub kind: &'static str,
}

#[derive(Serialize, Debug, Default, PartialEq)]
pub struct PlayerStats {
    pub hits: usize,
    /// Rallies won, from the `point` events; None when the replay has none
    pub points: Option<usize>,
    pub types: BTreeMap<&'static str, usize>,
    pub launch_speed_max: Option<f64>,
    pub launch_speed_mean: Option<f64>,
    pub head_speed_max: Option<f64>,
    pub head_speed_mean: Option<f64>,
    pub contact_height_mean: Option<f64>,
    pub fastest: Option<Fastest>,
    pub positions: Vec<Hit>,
}

#[derive(Serialize, Debug, PartialEq)]
pub struct Rally {
    pub start: f64,
    pub end: f64,
    pub shots: usize,
    pub duration: f64,
}

#[derive(Serialize, Debug, Default, PartialEq)]
pub struct RallySummary {
    pub count: usize,
    pub shots_mean: Option<f64>,
    pub shots_max: Option<usize>,
    pub duration_mean: Option<f64>,
    pub duration_max: Option<f64>,
    pub longest_start: Option<f64>,
    pub list: Vec<Rally>,
}

#[derive(Serialize, Debug, PartialEq)]
pub struct Stats {
    pub players: Vec<PlayerStats>,
    pub rallies: RallySummary,
    /// Per stroke type: the fastest launch of any player
    pub fastest_by_type: BTreeMap<&'static str, Fastest>,
}

fn mean_max(v: &[f64]) -> (Option<f64>, Option<f64>) {
    if v.is_empty() {
        return (None, None);
    }
    (
        Some(v.iter().sum::<f64>() / v.len() as f64),
        Some(v.iter().cloned().fold(f64::MIN, f64::max)),
    )
}

/// Court (x, z) of the player's feet at time `t`: ankle midpoint, else hip center (floor projection). None without joints.
fn foot_position(r: &Replay, t: f64, player: usize) -> Option<(f64, f64)> {
    if player >= r.players || r.frame_count == 0 {
        return None;
    }
    let f = ((t * r.fps as f64).round().max(0.0) as usize).min(r.frame_count - 1);
    let j = r.joints(f, player);
    let mid = |a: usize, b: usize| {
        let ok: Vec<&[f32; 4]> = [&j[a], &j[b]].into_iter().filter(|p| p[3] > 0.0).collect();
        (!ok.is_empty()).then(|| {
            let n = ok.len() as f64;
            (
                ok.iter().map(|p| p[0] as f64).sum::<f64>() / n,
                ok.iter().map(|p| p[2] as f64).sum::<f64>() / n,
            )
        })
    };
    mid(15, 16).or_else(|| mid(11, 12))
}

pub fn compute(r: &Replay) -> Stats {
    let hits: Vec<_> = r.strokes().iter().filter(|s| !s[3].is_nan()).collect();
    let mut players: Vec<PlayerStats> = (0..r.players.max(2))
        .map(|_| PlayerStats::default())
        .collect();
    let mut fastest_by_type: BTreeMap<&'static str, Fastest> = BTreeMap::new();
    let mut acc: Vec<[Vec<f64>; 3]> = vec![Default::default(); players.len()]; // launch, head, height
    for s in &hits {
        let p = s[4] as usize;
        if p >= players.len() {
            continue;
        }
        let (t, kind) = (
            s[3] as f64,
            STROKE_TYPES
                .get(s[5] as usize)
                .copied()
                .unwrap_or("unknown"),
        );
        let ps = &mut players[p];
        ps.hits += 1;
        *ps.types.entry(kind).or_insert(0) += 1;
        for (col, a) in [(12, 0), (7, 1), (11, 2)] {
            if s[col].is_finite() {
                acc[p][a].push(s[col] as f64);
            }
        }
        if s[12].is_finite() {
            let f = Fastest {
                speed: s[12] as f64,
                t,
                kind,
            };
            if ps.fastest.as_ref().is_none_or(|b| f.speed > b.speed) {
                ps.fastest = Some(Fastest { ..f });
            }
            if fastest_by_type.get(kind).is_none_or(|b| f.speed > b.speed) {
                fastest_by_type.insert(kind, f);
            }
        }
        if let Some((x, z)) = foot_position(r, t, p) {
            ps.positions.push(Hit { t, x, z, kind });
        }
    }
    for (ps, a) in players.iter_mut().zip(&acc) {
        (ps.launch_speed_mean, ps.launch_speed_max) = mean_max(&a[0]);
        (ps.head_speed_mean, ps.head_speed_max) = mean_max(&a[1]);
        ps.contact_height_mean = mean_max(&a[2]).0;
    }

    if r.events().iter().any(|e| e[1] == EVENT_POINT) {
        for ps in &mut players {
            ps.points = Some(0);
        }
        for e in r.events().iter().filter(|e| e[1] == EVENT_POINT) {
            if let Some(n) = players
                .get_mut(e[2] as usize)
                .and_then(|p| p.points.as_mut())
            {
                *n += 1;
            }
        }
    }

    let mut times: Vec<f64> = hits.iter().map(|s| s[3] as f64).collect();
    times.sort_by(f64::total_cmp);
    let mut list: Vec<Rally> = Vec::new();
    for &t in &times {
        match list.last_mut() {
            Some(l) if t - l.end <= RALLY_GAP_S => {
                l.end = t;
                l.shots += 1;
            }
            _ => list.push(Rally {
                start: t,
                end: t,
                shots: 1,
                duration: 0.0,
            }),
        }
    }
    for l in &mut list {
        l.duration = l.end - l.start;
    }
    let shots: Vec<f64> = list.iter().map(|l| l.shots as f64).collect();
    let durs: Vec<f64> = list.iter().map(|l| l.duration).collect();
    let rallies = RallySummary {
        count: list.len(),
        shots_mean: mean_max(&shots).0,
        shots_max: list.iter().map(|l| l.shots).max(),
        duration_mean: mean_max(&durs).0,
        duration_max: mean_max(&durs).1,
        longest_start: list
            .iter()
            .max_by(|a, b| a.duration.total_cmp(&b.duration))
            .map(|l| l.start),
        list,
    };
    Stats {
        players,
        rallies,
        fastest_by_type,
    }
}

pub fn to_json(r: &Replay) -> String {
    serde_json::to_string_pretty(&compute(r)).expect("stats serialise")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::replay::{JOINTS, STROKE_FIELDS};

    fn row(
        contact: f32,
        player: f32,
        kind: &str,
        head: f32,
        launch: f32,
        h: f32,
    ) -> [f32; STROKE_FIELDS] {
        let mut r = [f32::NAN; STROKE_FIELDS];
        r[0] = contact - 0.15;
        r[1] = contact;
        r[2] = contact + 0.15;
        r[3] = contact;
        r[4] = player;
        r[5] = STROKE_TYPES.iter().position(|t| *t == kind).unwrap() as f32;
        (r[7], r[12], r[11]) = (head, launch, h);
        r[14] = 0.0;
        r[15] = 0.0;
        r
    }

    fn replay() -> Replay {
        let n = 600;
        let mut joints = vec![[0.0; 4]; n * 2 * JOINTS];
        // Player 0 at frame 60: ankles at (1,_,2) and (3,_,4); player 1 at frame 120: only hips at (-2,_,1)
        let at = |f: usize, p: usize, j: usize| (f * 2 + p) * JOINTS + j;
        joints[at(60, 0, 15)] = [1.0, 0.0, 2.0, 1.0];
        joints[at(60, 0, 16)] = [3.0, 0.0, 4.0, 1.0];
        joints[at(120, 1, 11)] = [-2.0, 1.0, 1.0, 1.0];
        joints[at(120, 1, 12)] = [-2.0, 1.0, 1.0, 1.0];
        let strokes = vec![
            row(1.0, 0.0, "serve", f32::NAN, 20.0, 1.0),
            row(2.0, 1.0, "clear", f32::NAN, f32::NAN, 2.0),
            row(3.0, 0.0, "clear", 30.0, 40.0, 3.0),
            // gap 3.0 exactly is not a split, 3.5 is
            row(6.5, 1.0, "smash", 50.0, 90.0, 2.5),
            row(7.0, 0.0, "clear", f32::NAN, 30.0, 2.0),
        ];
        let mut air = row(4.0, 0.0, "swing_only", 99.0, f32::NAN, f32::NAN);
        air[3] = f32::NAN;
        Replay::from_parts(60.0, n, 2, joints, None).with_strokes([strokes, vec![air]].concat())
    }

    #[test]
    fn counts_speeds_and_nan_skipping() {
        let s = compute(&replay());
        assert_eq!((s.players[0].hits, s.players[1].hits), (3, 2));
        assert_eq!(s.players[0].types["clear"], 2);
        assert_eq!(s.players[0].launch_speed_max, Some(40.0));
        assert_eq!(s.players[0].launch_speed_mean, Some(30.0)); // (20 + 40 + 30) / 3
        assert_eq!(s.players[0].head_speed_max, Some(30.0)); // air swing's 99 is not a hit
        assert_eq!(s.players[1].launch_speed_mean, Some(90.0)); // NaN skipped
        assert_eq!(s.players[0].fastest.as_ref().map(|f| f.t), Some(3.0));
        assert_eq!(s.fastest_by_type["clear"].speed, 40.0);
        assert_eq!(s.players[0].contact_height_mean, Some(2.0));
    }

    #[test]
    fn rallies_split_over_three_seconds() {
        let s = compute(&replay());
        assert_eq!(s.rallies.count, 2);
        assert_eq!((s.rallies.list[0].shots, s.rallies.list[1].shots), (3, 2));
        assert_eq!(s.rallies.shots_max, Some(3));
        assert_eq!(s.rallies.duration_max, Some(2.0));
        assert_eq!(s.rallies.longest_start, Some(1.0));
    }

    #[test]
    fn positions_from_ankles_else_hips() {
        let s = compute(&replay());
        let p0 = &s.players[0].positions[0];
        assert_eq!((p0.t, p0.x, p0.z), (1.0, 2.0, 3.0));
        // Player 1's first hit (t = 2.0, frame 120) has hips only
        let p1 = &s.players[1].positions[0];
        assert_eq!((p1.x, p1.z), (-2.0, 1.0));
        // Frame 360 has no joints at all: the hit has no position
        assert_eq!(s.players[1].positions.len(), 1);
    }

    #[test]
    fn points_counted_per_winner() {
        let pt = |t: f32, w: f32| [t, EVENT_POINT, w, 0.0, 0.0, 0.0];
        let r = replay().with_shuttle(
            vec![[0.0; 8]; 600],
            vec![pt(1.0, 0.0), pt(2.0, 1.0), pt(3.0, 1.0)],
        );
        let s = compute(&r);
        assert_eq!(
            (s.players[0].points, s.players[1].points),
            (Some(1), Some(2))
        );
        assert_eq!(compute(&replay()).players[0].points, None);
    }

    #[test]
    fn empty_replay_is_zeros_and_nulls() {
        let r = Replay::from_parts(60.0, 0, 2, vec![], None);
        let s = compute(&r);
        assert_eq!(s.players.len(), 2);
        assert!(
            s.players
                .iter()
                .all(|p| p.hits == 0 && p.fastest.is_none() && p.head_speed_max.is_none())
        );
        assert_eq!((s.rallies.count, s.rallies.longest_start), (0, None));
        assert!(to_json(&r).contains("null"));
    }
}
