//! Replay file v1 (`assets/replay/replay.bin`, written by tools/pose3d.py and src/bin/oso-imu.rs; format in
//! docs/data-format.md). The `joints3d` and (optional) `racquet`, `shuttle` and `events` tracks are read; unknown tracks are ignored so
//! later phases can add their own. [`Container`] reads and writes any tracks without interpreting them.

use serde::Deserialize;
use serde_json::{Map, Value};

pub const JOINTS: usize = 17;
/// `racquet` track fields per player and frame: px, py, pz, qx, qy, qz, qw, conf, head_speed
pub const RACQUET_FIELDS: usize = 9;
/// `shuttle` track fields per frame: x, y, z, vx, vy, vz, conf, flight_id (-1 outside flights)
pub const SHUTTLE_FIELDS: usize = 8;
/// `events` track fields per event: t, type, player, x, y, z
pub const EVENT_FIELDS: usize = 6;
pub const EVENT_CONTACT: f32 = 1.0;
pub const EVENT_LANDING: f32 = 2.0;
/// End of a rally: player = winner, x = winner's score, y = the other player's score (the winner serves next), z = 0
pub const EVENT_POINT: f32 = 3.0;

/// Scores `[player 0, player 1]` after the last point event at or before `t` (`[0, 0]` before the first); None when
/// `events` holds no point event at all.
pub fn score_in(events: &[[f32; EVENT_FIELDS]], t: f64) -> Option<[u32; 2]> {
    let mut points = events.iter().filter(|e| e[1] == EVENT_POINT).peekable();
    points.peek()?;
    let mut s = [0, 0];
    for e in points.take_while(|e| e[0] as f64 <= t) {
        let w = (e[2] as usize).min(1);
        (s[w], s[1 - w]) = (e[3] as u32, e[4] as u32);
    }
    Some(s)
}
/// `swings` track columns per stroke: t_start, t_peak, t_end, t_contact, player, stroke_type, peak_omega_dps,
/// head_speed_peak, head_speed_rot, impact_g, timing_ms, contact_h, launch_speed, launch_angle_deg, saturated, confidence
pub const STROKE_FIELDS: usize = 16;
/// Names of the `swings` track's `stroke_type` column, in index order
pub const STROKE_TYPES: [&str; 14] = [
    "unknown",
    "serve",
    "clear",
    "smash",
    "drop",
    "drive",
    "lift",
    "net",
    "swing_only",
    "block",
    "push",
    "press",
    "net_kill",
    "flick_serve",
];
/// Jumping to a stroke lands this long before its contact
pub const STROKE_LEAD_S: f64 = 0.4;

/// COCO-17 bones as joint index pairs.
/// NOTE: mirrors tools/skeleton.py; both files must change together.
pub const BONES: [(usize, usize); 16] = [
    (0, 1),
    (0, 2),
    (1, 3),
    (2, 4),
    (5, 6),
    (5, 7),
    (7, 9),
    (6, 8),
    (8, 10),
    (5, 11),
    (6, 12),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
];

#[derive(Deserialize)]
struct Header {
    version: u32,
    fps: f32,
    frame_count: usize,
    tracks: Vec<TrackHeader>,
}

#[derive(Deserialize)]
struct TrackHeader {
    name: String,
    dtype: String,
    shape: Vec<usize>,
    offset: usize,
}

pub struct Replay {
    pub fps: f32,
    pub frame_count: usize,
    pub players: usize,
    // [frame][player][joint] flattened; each joint is x, y, z, conf (court meters)
    joints: Vec<[f32; 4]>,
    // [frame][player] flattened, if the file has a racquet track
    racquet: Option<Vec<[f32; RACQUET_FIELDS]>>,
    // [frame] if the file has a shuttle track
    shuttle: Option<Vec<[f32; SHUTTLE_FIELDS]>>,
    // Not per frame: its length has nothing to do with frame_count
    events: Vec<[f32; EVENT_FIELDS]>,
    // Not per frame; sorted by time
    strokes: Vec<[f32; STROKE_FIELDS]>,
}

/// One point of the shuttle trail: position, conf (1 detected, 0.5 physics-filled) and age in seconds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TrailPoint {
    pub position: [f32; 3],
    pub conf: f32,
    pub age: f32,
}

/// Racquet pose at one instant: the butt of the handle in court meters, orientation of the racquet frame
/// (+Y butt to tip, +Z string-bed normal).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RacquetPose {
    pub position: cgmath::Vector3<f32>,
    pub rotation: cgmath::Quaternion<f32>,
    pub conf: f32,
    /// m/s at the head center
    pub head_speed: f32,
}

impl Replay {
    pub fn parse(bytes: &[u8]) -> anyhow::Result<Replay> {
        anyhow::ensure!(
            bytes.len() >= 8 && &bytes[..4] == b"OSOR",
            "bad replay magic"
        );
        let n = u32::from_le_bytes(bytes[4..8].try_into()?) as usize;
        let head = bytes
            .get(8..8 + n)
            .ok_or_else(|| anyhow::anyhow!("truncated replay header"))?;
        // The writer zero-pads the JSON
        let end = head.iter().rposition(|&b| b != 0).map_or(0, |i| i + 1);
        let header: Header = serde_json::from_slice(&head[..end])?;
        anyhow::ensure!(
            header.version == 1,
            "unsupported replay version {}",
            header.version
        );
        let blob = bytes.get((8 + n).next_multiple_of(16)..).unwrap_or(&[]);

        let t = header
            .tracks
            .iter()
            .find(|t| t.name == "joints3d")
            .ok_or_else(|| anyhow::anyhow!("replay has no joints3d track"))?;
        anyhow::ensure!(
            t.dtype == "f32"
                && t.shape.len() == 4
                && t.shape[0] == header.frame_count
                && t.shape[2] == JOINTS
                && t.shape[3] == 4,
            "unexpected joints3d layout: {} {:?}",
            t.dtype,
            t.shape
        );
        let players = t.shape[1];
        // Copy: the byte slice carries no alignment guarantee
        let joints = bytemuck::pod_collect_to_vec::<u8, [f32; 4]>(track_bytes(blob, t)?);

        let racquet = match header.tracks.iter().find(|t| t.name == "racquet") {
            None => None,
            Some(t) => {
                anyhow::ensure!(
                    t.dtype == "f32" && t.shape == [header.frame_count, players, RACQUET_FIELDS],
                    "unexpected racquet layout: {} {:?}",
                    t.dtype,
                    t.shape
                );
                Some(bytemuck::pod_collect_to_vec::<u8, [f32; RACQUET_FIELDS]>(
                    track_bytes(blob, t)?,
                ))
            }
        };
        let shuttle = match header.tracks.iter().find(|t| t.name == "shuttle") {
            None => None,
            Some(t) => {
                anyhow::ensure!(
                    t.dtype == "f32" && t.shape == [header.frame_count, SHUTTLE_FIELDS],
                    "unexpected shuttle layout: {} {:?}",
                    t.dtype,
                    t.shape
                );
                Some(bytemuck::pod_collect_to_vec::<u8, [f32; SHUTTLE_FIELDS]>(
                    track_bytes(blob, t)?,
                ))
            }
        };
        let events = match header.tracks.iter().find(|t| t.name == "events") {
            None => Vec::new(),
            Some(t) => {
                anyhow::ensure!(
                    t.dtype == "f32" && t.shape.len() == 2 && t.shape[1] == EVENT_FIELDS,
                    "unexpected events layout: {} {:?}",
                    t.dtype,
                    t.shape
                );
                bytemuck::pod_collect_to_vec::<u8, [f32; EVENT_FIELDS]>(track_bytes(blob, t)?)
            }
        };
        // Unknown-tolerant: a swings track with another column count (a later format) is ignored, not an error
        let strokes = match header.tracks.iter().find(|t| t.name == "swings") {
            Some(t) if t.dtype == "f32" && t.shape.len() == 2 && t.shape[1] == STROKE_FIELDS => {
                bytemuck::pod_collect_to_vec::<u8, [f32; STROKE_FIELDS]>(track_bytes(blob, t)?)
            }
            _ => Vec::new(),
        };
        Ok(Replay {
            fps: header.fps,
            frame_count: header.frame_count,
            players,
            joints,
            racquet,
            shuttle,
            events,
            strokes,
        })
    }

    /// In-memory replay (tests, benches, tools that synthesize one).
    pub fn from_parts(
        fps: f32,
        frame_count: usize,
        players: usize,
        joints: Vec<[f32; 4]>,
        racquet: Option<Vec<[f32; RACQUET_FIELDS]>>,
    ) -> Replay {
        assert_eq!(joints.len(), frame_count * players * JOINTS);
        assert!(
            racquet
                .as_ref()
                .is_none_or(|r| r.len() == frame_count * players)
        );
        Replay {
            fps,
            frame_count,
            players,
            joints,
            racquet,
            shuttle: None,
            events: Vec::new(),
            strokes: Vec::new(),
        }
    }

    /// Adds a `swings` track to an in-memory replay (tests, benches).
    pub fn with_strokes(mut self, strokes: Vec<[f32; STROKE_FIELDS]>) -> Replay {
        self.strokes = strokes;
        self
    }

    /// Strokes (see [`STROKE_FIELDS`]), sorted by time.
    pub fn strokes(&self) -> &[[f32; STROKE_FIELDS]] {
        &self.strokes
    }

    /// Where jumping to a stroke lands: `STROKE_LEAD_S` before its contact, or its start for an air swing.
    pub fn stroke_seek_time(row: &[f32; STROKE_FIELDS]) -> f64 {
        if row[3].is_nan() {
            row[0] as f64
        } else {
            (row[3] as f64 - STROKE_LEAD_S).max(0.0)
        }
    }

    fn seek_times(&self) -> Vec<f64> {
        let mut v: Vec<f64> = self.strokes.iter().map(Self::stroke_seek_time).collect();
        v.sort_by(f64::total_cmp);
        v
    }

    /// Seek target of the first stroke after playback time `t` (a stroke you are already on does not count), if any.
    pub fn next_stroke(&self, t: f64) -> Option<f64> {
        next_time(&self.seek_times(), t + SEEK_EPS)
    }

    /// Seek target of the last stroke before `t`.
    pub fn prev_stroke(&self, t: f64) -> Option<f64> {
        prev_time(&self.seek_times(), t - SEEK_EPS)
    }

    /// Per-frame head speed (m/s) of `player`'s racquet; NaN where the racquet has conf 0 or there is no track.
    pub fn head_speed(&self, player: usize) -> Vec<f32> {
        (0..self.frame_count)
            .map(|f| match self.racquet(f, player) {
                Some(r) if r[7] > 0.0 => r[8],
                _ => f32::NAN,
            })
            .collect()
    }

    /// Adds shuttle and event tracks to an in-memory replay (tests, benches).
    pub fn with_shuttle(
        mut self,
        shuttle: Vec<[f32; SHUTTLE_FIELDS]>,
        events: Vec<[f32; EVENT_FIELDS]>,
    ) -> Replay {
        assert_eq!(shuttle.len(), self.frame_count);
        self.shuttle = Some(shuttle);
        self.events = events;
        self
    }

    pub fn has_shuttle(&self) -> bool {
        self.shuttle.is_some()
    }

    /// Match score at `t` from the `point` events; None when the replay has none.
    pub fn score_at(&self, t: f64) -> Option<[u32; 2]> {
        score_in(&self.events, t)
    }

    /// Contact and landing events: t, type (1 contact, 2 floor landing), player (-1 for landings), x, y, z.
    pub fn events(&self) -> &[[f32; EVENT_FIELDS]] {
        &self.events
    }

    /// Raw shuttle entry (x, y, z, vx, vy, vz, conf, flight_id), if the file has the track.
    pub fn shuttle(&self, frame: usize) -> Option<&[f32; SHUTTLE_FIELDS]> {
        self.shuttle.as_ref().map(|s| &s[frame])
    }

    /// Shuttle (position, velocity, conf) at playback time `t`: linear between the two surrounding frames while both
    /// are in the same flight; across a flight change (a contact) or into a frame without a sample the nearer frame
    /// is used. None when that frame has conf 0 (hidden).
    pub fn sample_shuttle(
        &self,
        t: f64,
    ) -> Option<(cgmath::Vector3<f32>, cgmath::Vector3<f32>, f32)> {
        let f = t * self.fps as f64;
        let frac = (f - f.floor()) as f32;
        let i0 = frame_at(t, self.fps, self.frame_count);
        let a = self.shuttle(i0)?;
        let b = self.shuttle((i0 + 1).min(self.frame_count - 1))?;
        let get = |r: &[f32; SHUTTLE_FIELDS]| {
            (
                cgmath::Vector3::new(r[0], r[1], r[2]),
                cgmath::Vector3::new(r[3], r[4], r[5]),
                r[6],
            )
        };
        if a[6] <= 0.0 || b[6] <= 0.0 || a[7] != b[7] {
            let near = if frac < 0.5 { a } else { b };
            return (near[6] > 0.0).then(|| get(near));
        }
        let ((pa, va, ca), (pb, vb, cb)) = (get(a), get(b));
        use cgmath::VectorSpace;
        Some((pa.lerp(pb, frac), va.lerp(vb, frac), ca.min(cb)))
    }

    /// Fills `out` (cleared first) with the shuttle's path over the last `window` seconds before `t`, newest first:
    /// the current sample, then every frame inside the window of the same flight. Empty when the shuttle is hidden.
    pub fn shuttle_trail(&self, t: f64, window: f32, out: &mut Vec<TrailPoint>) {
        out.clear();
        let Some((pos, _, conf)) = self.sample_shuttle(t) else {
            return;
        };
        let i0 = frame_at(t, self.fps, self.frame_count);
        let Some(cur) = self.shuttle(i0) else { return };
        // Equal ids when interpolating; across a flight change the sample came from the nearer frame
        let next = self.shuttle((i0 + 1).min(self.frame_count - 1)).unwrap();
        let frac = (t * self.fps as f64).rem_euclid(1.0) as f32;
        let flight = if frac < 0.5 { cur[7] } else { next[7] };
        out.push(TrailPoint {
            position: pos.into(),
            conf,
            age: 0.0,
        });
        // Frame times are exact multiples of 1/fps; t may sit inside the frame i0
        let t_wrapped = wrap_time(t, self.fps, self.frame_count);
        for i in (0..=i0).rev() {
            let age = (t_wrapped - i as f64 / self.fps as f64) as f32;
            if age > window {
                break;
            }
            let r = self.shuttle(i).unwrap();
            if r[6] <= 0.0 || r[7] != flight {
                break;
            }
            if age > 1e-6 {
                out.push(TrailPoint {
                    position: [r[0], r[1], r[2]],
                    conf: r[6],
                    age,
                });
            }
        }
    }

    pub fn has_racquet(&self) -> bool {
        self.racquet.is_some()
    }

    /// Raw racquet entry (px, py, pz, qx, qy, qz, qw, conf, head_speed), if the file has the track.
    pub fn racquet(&self, frame: usize, player: usize) -> Option<&[f32; RACQUET_FIELDS]> {
        self.racquet
            .as_ref()
            .map(|r| &r[frame * self.players + player])
    }

    /// Racquet at playback time `t` (looping like `sample`): position linear, orientation slerp. When either
    /// surrounding frame has conf 0 the nearer frame is used; None if that one is missing too (hide the racquet).
    pub fn sample_racquet(&self, t: f64, player: usize) -> Option<RacquetPose> {
        let f = t * self.fps as f64;
        let frac = (f - f.floor()) as f32;
        let i0 = frame_at(t, self.fps, self.frame_count);
        let a = self.racquet(i0, player)?;
        let b = self.racquet((i0 + 1).min(self.frame_count - 1), player)?;
        let pose = |r: &[f32; RACQUET_FIELDS]| RacquetPose {
            position: cgmath::Vector3::new(r[0], r[1], r[2]),
            rotation: cgmath::Quaternion::new(r[6], r[3], r[4], r[5]),
            conf: r[7],
            head_speed: r[8],
        };
        let (pa, pb) = (pose(a), pose(b));
        if pa.conf <= 0.0 || pb.conf <= 0.0 {
            let near = if frac < 0.5 { pa } else { pb };
            return (near.conf > 0.0).then_some(near);
        }
        use cgmath::{InnerSpace, VectorSpace};
        // Shortest arc: q and -q are the same rotation
        let neg = pa.rotation.dot(pb.rotation) < 0.0;
        let qb = if neg { -pb.rotation } else { pb.rotation };
        Some(RacquetPose {
            position: pa.position.lerp(pb.position, frac),
            rotation: pa.rotation.slerp(qb, frac).normalize(),
            conf: pa.conf.min(pb.conf),
            head_speed: pa.head_speed + (pb.head_speed - pa.head_speed) * frac,
        })
    }

    pub fn joints(&self, frame: usize, player: usize) -> &[[f32; 4]; JOINTS] {
        let start = (frame * self.players + player) * JOINTS;
        self.joints[start..start + JOINTS].try_into().unwrap()
    }

    /// Joints at playback time `t` (any value, looping), linearly interpolated between the two
    /// surrounding frames. A joint missing (conf 0) in either frame takes the nearer frame's value
    /// instead of blending toward the zero position.
    pub fn sample(&self, t: f64, player: usize) -> [[f32; 4]; JOINTS] {
        let f = t * self.fps as f64;
        let frac = (f - f.floor()) as f32;
        let i0 = frame_at(t, self.fps, self.frame_count);
        let (a, b) = (
            self.joints(i0, player),
            // Clamped, not wrapped: the loop seam would blend the last pose into the first
            self.joints((i0 + 1).min(self.frame_count - 1), player),
        );
        std::array::from_fn(|j| {
            let (ja, jb) = (a[j], b[j]);
            if ja[3] <= 0.0 || jb[3] <= 0.0 {
                return if frac < 0.5 { ja } else { jb };
            }
            let l = |k: usize| ja[k] + (jb[k] - ja[k]) * frac;
            [l(0), l(1), l(2), ja[3].min(jb[3])]
        })
    }
}

fn track_bytes<'a>(blob: &'a [u8], t: &TrackHeader) -> anyhow::Result<&'a [u8]> {
    anyhow::ensure!(
        t.offset.is_multiple_of(16),
        "track {} offset {} not 16-aligned",
        t.name,
        t.offset
    );
    let len = t.shape.iter().product::<usize>() * 4;
    blob.get(t.offset..t.offset + len)
        .ok_or_else(|| anyhow::anyhow!("{} data out of bounds", t.name))
}

/// One-line summary of a `swings` row (log lines and the web tooltips use the same fields).
pub fn stroke_summary(row: &[f32; STROKE_FIELDS]) -> String {
    let mut s = format!(
        "stroke {} player {} at {:.2} s",
        STROKE_TYPES.get(row[5] as usize).unwrap_or(&"?"),
        row[4],
        if row[3].is_nan() { row[1] } else { row[3] }
    );
    if !row[7].is_nan() {
        s += &format!(", head {:.0} km/h", row[7] * 3.6);
    }
    if !row[6].is_nan() {
        s += &format!(", peak {:.0} dps", row[6]);
    }
    if !row[10].is_nan() {
        s += &format!(", impact {:.1} g, timing {:+.0} ms", row[9], row[10]);
    }
    s
}

/// Pure seek logic shared by N/P keys and the buttons: the stroke after / before `t` in a sorted list of stroke times.
/// A stroke exactly at `t` is neither next nor previous. None at the ends.
pub fn next_time(times: &[f64], t: f64) -> Option<f64> {
    times.iter().copied().find(|&x| x > t)
}

pub fn prev_time(times: &[f64], t: f64) -> Option<f64> {
    times.iter().rev().copied().find(|&x| x < t)
}

/// Landing on a stroke's seek time and pressing next/prev again moves on (seconds)
const SEEK_EPS: f64 = 1e-4;

/// A replay file as raw JSON header fields plus raw track bytes: lets a tool add or replace one track and rewrite the
/// file with every other track byte-identical. Only f32 tracks (the only dtype in format v1).
pub struct Container {
    /// Every top-level header field except `tracks`
    pub header: Map<String, Value>,
    /// (track header minus `offset`, track data without padding), in file order
    pub tracks: Vec<(Map<String, Value>, Vec<u8>)>,
}

impl Container {
    pub fn read(bytes: &[u8]) -> anyhow::Result<Container> {
        anyhow::ensure!(
            bytes.len() >= 8 && &bytes[..4] == b"OSOR",
            "bad replay magic"
        );
        let n = u32::from_le_bytes(bytes[4..8].try_into()?) as usize;
        let head = bytes
            .get(8..8 + n)
            .ok_or_else(|| anyhow::anyhow!("truncated replay header"))?;
        let end = head.iter().rposition(|&b| b != 0).map_or(0, |i| i + 1);
        let Value::Object(mut header) = serde_json::from_slice(&head[..end])? else {
            anyhow::bail!("replay header is not an object");
        };
        let blob = bytes.get((8 + n).next_multiple_of(16)..).unwrap_or(&[]);
        let Some(Value::Array(metas)) = header.remove("tracks") else {
            anyhow::bail!("replay header has no tracks array");
        };
        let mut tracks = Vec::new();
        for meta in metas {
            let Value::Object(mut meta) = meta else {
                anyhow::bail!("track header is not an object");
            };
            let th: TrackHeader = serde_json::from_value(Value::Object(meta.clone()))?;
            anyhow::ensure!(th.dtype == "f32", "track {} is not f32", th.name);
            let data = track_bytes(blob, &th)?.to_vec();
            meta.remove("offset");
            tracks.push((meta, data));
        }
        Ok(Container { header, tracks })
    }

    /// Replaces the track named `meta["name"]` or appends it. `meta` needs `name` and `shape`.
    pub fn set_track(&mut self, meta: Map<String, Value>, data: &[f32]) {
        let bytes = data.iter().flat_map(|v| v.to_le_bytes()).collect();
        let name = meta.get("name").cloned();
        match self
            .tracks
            .iter_mut()
            .find(|(m, _)| m.get("name") == name.as_ref())
        {
            Some(slot) => *slot = (meta, bytes),
            None => self.tracks.push((meta, bytes)),
        }
    }

    pub fn write(&self) -> Vec<u8> {
        let tracks: Vec<_> = self
            .tracks
            .iter()
            .map(|(m, d)| (m.clone(), d.as_slice()))
            .collect();
        write_tracks(&self.header, &tracks)
    }
}

/// Serializes a replay file, mirroring tools/replay.py: header JSON zero-padded so that (8 + len) % 16 == 0, each
/// track zero-padded to 16 bytes. `header` holds everything but `tracks`; each track's `dtype` is set to f32 and its
/// `offset` filled in here (shape comes from the caller's track header).
pub fn write_tracks(
    header: &Map<String, Value>,
    tracks: &[(Map<String, Value>, &[u8])],
) -> Vec<u8> {
    let mut blob = Vec::new();
    let mut metas = Vec::new();
    for (meta, data) in tracks {
        let mut meta = meta.clone();
        meta.insert("dtype".into(), "f32".into());
        meta.insert("offset".into(), blob.len().into());
        metas.push(Value::Object(meta));
        blob.extend_from_slice(data);
        blob.resize(blob.len().next_multiple_of(16), 0);
    }
    let mut header = header.clone();
    header.insert("tracks".into(), Value::Array(metas));
    let mut head = serde_json::to_vec(&Value::Object(header)).expect("json serializes");
    head.resize(head.len() + (8 + head.len()).wrapping_neg() % 16, 0);
    let mut out = b"OSOR".to_vec();
    out.extend((head.len() as u32).to_le_bytes());
    out.extend(head);
    out.extend(blob);
    out
}

/// Playback time (seconds, any value) -> frame index, looping.
pub fn frame_at(t: f64, fps: f32, frame_count: usize) -> usize {
    let f = (t * fps as f64).floor() as i64;
    f.rem_euclid(frame_count.max(1) as i64) as usize
}

/// Wraps playback time into [0, duration).
pub fn wrap_time(t: f64, fps: f32, frame_count: usize) -> f64 {
    t.rem_euclid((frame_count.max(1) as f64) / fps as f64)
}

/// Frame `delta` steps from `frame`, looping.
pub fn step_frame(frame: usize, delta: i64, frame_count: usize) -> usize {
    (frame as i64 + delta).rem_euclid(frame_count.max(1) as i64) as usize
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &[u8] = include_bytes!("../../../assets/replay/replay.bin");

    fn build(header: &str, blob: &[u8]) -> Vec<u8> {
        let mut h = header.as_bytes().to_vec();
        h.resize(h.len() + (8 + h.len()).wrapping_neg() % 16, 0);
        let mut out = b"OSOR".to_vec();
        out.extend((h.len() as u32).to_le_bytes());
        out.extend(h);
        out.extend(blob);
        out
    }

    fn header(tracks: &str, version: u32) -> String {
        format!(r#"{{"version":{version},"fps":60.0,"frame_count":2,"tracks":[{tracks}]}}"#)
    }

    const JOINTS_TRACK: &str =
        r#"{"name":"joints3d","dtype":"f32","shape":[2,1,17,4],"offset":OFF}"#;

    fn blob() -> Vec<u8> {
        (0..2 * 17 * 4)
            .flat_map(|i| (i as f32).to_le_bytes())
            .collect()
    }

    #[test]
    fn parses_sample() {
        let r = Replay::parse(SAMPLE).unwrap();
        assert_eq!((r.frame_count, r.players, r.fps), (600, 2, 60.0));
        assert_eq!(r.joints(599, 1).len(), JOINTS);
        // Written by oso-imu: sorted strokes, every shuttle contact among them
        let n = r.strokes().len();
        let contacts = r.events().iter().filter(|e| e[1] == EVENT_CONTACT).count();
        assert!(n >= contacts && contacts > 0);
        assert!(r.strokes().windows(2).all(|w| w[0][1] <= w[1][1]));
        assert!(r.next_stroke(0.0).is_some());
        // Feet near the floor, bodies inside the court
        let ankle = r.joints(100, 0)[15];
        assert!(
            ankle[3] > 0.0 && ankle[1] < 0.5 && ankle[0] < 0.0,
            "{ankle:?}"
        );
    }

    #[test]
    fn indexes_frame_player_joint() {
        let h = header(&JOINTS_TRACK.replace("OFF", "0"), 1);
        let r = Replay::parse(&build(&h, &blob())).unwrap();
        assert_eq!(r.joints(1, 0)[2], [76.0, 77.0, 78.0, 79.0]);
    }

    #[test]
    fn ignores_unknown_tracks_and_unaligned_input() {
        let tracks = format!(
            r#"{{"name":"future_track","dtype":"f32","shape":[2,3],"offset":0}},{}"#,
            JOINTS_TRACK.replace("OFF", "32")
        );
        let mut data = vec![0u8; 32];
        data.extend(blob());
        let mut bytes = vec![0u8]; // shift the file by one byte: parse must not assume alignment of the slice
        bytes.extend(build(&header(&tracks, 1), &data));
        assert!(Replay::parse(&bytes[1..]).is_ok());
    }

    #[test]
    fn rejects_bad_input() {
        let ok = header(&JOINTS_TRACK.replace("OFF", "0"), 1);
        let mut bad_magic = build(&ok, &blob());
        bad_magic[0] = b'X';
        assert!(Replay::parse(&bad_magic).is_err());
        assert!(
            Replay::parse(&build(
                &header(&JOINTS_TRACK.replace("OFF", "0"), 2),
                &blob()
            ))
            .is_err()
        );
        assert!(
            Replay::parse(&build(
                &header(&JOINTS_TRACK.replace("OFF", "8"), 1),
                &blob()
            ))
            .is_err()
        );
        assert!(Replay::parse(&build(&header("", 1), &blob())).is_err());
        assert!(Replay::parse(&build(&ok, &blob()[..100])).is_err());
        assert!(Replay::parse(b"").is_err());
    }

    #[test]
    fn playback_frames_loop() {
        assert_eq!(frame_at(0.0, 60.0, 600), 0);
        assert_eq!(frame_at(1.0 / 60.0 * 5.5, 60.0, 600), 5);
        assert_eq!(frame_at(10.0 + 0.01, 60.0, 600), 0);
        assert_eq!(frame_at(-0.001, 60.0, 600), 599);
        assert_eq!(step_frame(599, 1, 600), 0);
        assert_eq!(step_frame(0, -1, 600), 599);
        assert_eq!(step_frame(10, 1, 600), 11);
        assert!((wrap_time(10.5, 60.0, 600) - 0.5).abs() < 1e-9);
    }

    #[test]
    fn sample_interpolates_and_keeps_missing_joints_put() {
        let h = header(&JOINTS_TRACK.replace("OFF", "0"), 1);
        let mut r = Replay::parse(&build(&h, &blob())).unwrap();
        // frame 0 joint 0 = (0,1,2,3), frame 1 joint 0 = (68,69,70,71): conf 3 and 71
        let mid = r.sample(0.5 / 60.0, 0)[0];
        assert_eq!(mid, [34.0, 35.0, 36.0, 3.0]);
        // Last frame holds instead of blending into frame 0
        assert_eq!(r.sample(1.5 / 60.0, 0)[0], [68.0, 69.0, 70.0, 71.0]);
        assert_eq!(r.sample(0.0, 0)[0], [0.0, 1.0, 2.0, 3.0]);
        // Frame 1 joint 0 becomes missing at the origin: never blend toward it
        r.joints[JOINTS] = [0.0; 4];
        let near0 = r.sample(0.25 / 60.0, 0)[0];
        assert_eq!(near0, [0.0, 1.0, 2.0, 3.0]);
        assert_eq!(r.sample(0.75 / 60.0, 0)[0][3], 0.0);
    }

    fn racquet_replay(frames: &[[[f32; RACQUET_FIELDS]; 1]]) -> Replay {
        let n = frames.len();
        Replay::from_parts(
            60.0,
            n,
            1,
            vec![[0.0; 4]; n * JOINTS],
            Some(frames.iter().map(|f| f[0]).collect()),
        )
    }

    #[test]
    fn sample_racquet_slerps_and_hides() {
        let h = std::f32::consts::FRAC_1_SQRT_2;
        // frame 0: identity at x = 0; frame 1: 90 deg about +Y at x = 2 (stored as the negated quaternion: same rotation)
        let r = racquet_replay(&[
            [[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 4.0]],
            [[2.0, 1.0, 0.0, 0.0, -h, 0.0, -h, 0.5, 8.0]],
            [[0.0; RACQUET_FIELDS]],
        ]);
        let p = r.sample_racquet(0.5 / 60.0, 0).unwrap();
        assert!(
            (p.position.x - 1.0).abs() < 1e-6 && p.conf == 0.5 && (p.head_speed - 6.0).abs() < 1e-6
        );
        // 45 degrees about Y: (0, sin 22.5, 0, cos 22.5), shortest arc despite the flipped sign
        let (s, c) = (22.5f32.to_radians().sin(), 22.5f32.to_radians().cos());
        let q = p.rotation;
        assert!(
            (q.v.y - s).abs() < 1e-5 && (q.s - c).abs() < 1e-5 && q.v.x == 0.0,
            "{q:?}"
        );
        // Next to a conf 0 frame: the nearer frame wins, and a missing one hides the racquet
        assert_eq!(r.sample_racquet(1.25 / 60.0, 0).unwrap().position.x, 2.0);
        assert!(r.sample_racquet(1.75 / 60.0, 0).is_none());
        // Without the track there is nothing to draw
        let none = Replay::parse(&build(
            &header(&JOINTS_TRACK.replace("OFF", "0"), 1),
            &blob(),
        ))
        .unwrap();
        assert!(!none.has_racquet() && none.sample_racquet(0.0, 0).is_none());
    }

    #[test]
    fn container_adds_a_track_and_keeps_the_rest_byte_identical() {
        let mut c = Container::read(SAMPLE).unwrap();
        // The shipped file may already carry a racquet track: start from joints3d alone
        c.tracks.retain(|t| t.0["name"] == "joints3d");
        assert_eq!(c.tracks.len(), 1);
        let joints_bytes = c.tracks[0].1.clone();
        let (frames, players) = (c.header["frame_count"].as_u64().unwrap() as usize, 2);
        let data: Vec<f32> = (0..frames * players * RACQUET_FIELDS)
            .map(|i| i as f32 * 0.25)
            .collect();
        let mut meta = Map::new();
        meta.insert("name".into(), "racquet".into());
        meta.insert(
            "shape".into(),
            serde_json::json!([frames, players, RACQUET_FIELDS]),
        );
        c.set_track(meta.clone(), &data);
        // Setting it again replaces, not appends
        c.set_track(meta, &data);
        assert_eq!(c.tracks.len(), 2);
        let bytes = c.write();
        // Layout rules shared with tools/replay.py
        let n = u32::from_le_bytes(bytes[4..8].try_into().unwrap()) as usize;
        assert_eq!((8 + n) % 16, 0);
        let r = Replay::parse(&bytes).unwrap();
        let orig = Replay::parse(SAMPLE).unwrap();
        assert_eq!(
            (r.frame_count, r.players, r.fps),
            (orig.frame_count, orig.players, orig.fps)
        );
        assert_eq!(r.joints(300, 1), orig.joints(300, 1));
        assert_eq!(
            r.racquet(1, 1).unwrap()[0],
            (RACQUET_FIELDS * 3) as f32 * 0.25
        );
        // Reading the written file back gives the same raw tracks
        let back = Container::read(&bytes).unwrap();
        assert_eq!(back.tracks[0].1, joints_bytes);
        assert_eq!(back.tracks[1].1.len(), data.len() * 4);
        assert_eq!(back.header, c.header);
        // Idempotent
        assert_eq!(back.write(), bytes);
    }

    #[test]
    fn write_tracks_pads_like_the_python_writer() {
        let mut header = Map::new();
        header.insert("version".into(), 1.into());
        header.insert("fps".into(), 60.0.into());
        header.insert("frame_count".into(), 3.into());
        let t = |name: &str, shape: &[usize]| {
            let mut m = Map::new();
            m.insert("name".into(), name.into());
            m.insert("shape".into(), serde_json::json!(shape));
            m
        };
        // 17 f32 = 68 bytes -> padded to 80 before the next track
        let a = vec![1u8; 68];
        let b = vec![2u8; 4];
        let bytes = write_tracks(&header, &[(t("a", &[17]), &a), (t("b", &[1]), &b)]);
        let c = Container::read(&bytes);
        // No joints3d, so it is a container but not a Replay
        assert!(Replay::parse(&bytes).is_err());
        let c = c.unwrap();
        assert_eq!((c.tracks[0].1.len(), c.tracks[1].1.len()), (68, 4));
        let n = u32::from_le_bytes(bytes[4..8].try_into().unwrap()) as usize;
        let head: Value =
            serde_json::from_slice(bytes[8..8 + n].split(|&b| b == 0).next().unwrap()).unwrap();
        assert_eq!(head["tracks"][0]["offset"], 0);
        assert_eq!(head["tracks"][1]["offset"], 80);
        assert_eq!(bytes.len(), 8 + n + 80 + 16);
    }

    fn stroke_row(t_contact: f32, t_start: f32) -> [f32; STROKE_FIELDS] {
        let mut r = [f32::NAN; STROKE_FIELDS];
        r[0] = t_start;
        r[3] = t_contact;
        r
    }

    #[test]
    fn next_and_prev_stroke_at_the_boundaries() {
        // Seek targets: 0.6 (contact 1.0), 1.6 (contact 2.0), and an air swing starting at 3.0
        let r = Replay::from_parts(60.0, 1, 1, vec![[0.0; 4]; JOINTS], None).with_strokes(vec![
            stroke_row(1.0, 0.85),
            stroke_row(2.0, 1.85),
            stroke_row(f32::NAN, 3.0),
        ]);
        assert_eq!(Replay::stroke_seek_time(&r.strokes()[2]), 3.0);
        assert!((r.next_stroke(0.0).unwrap() - 0.6).abs() < 1e-6);
        assert_eq!(r.prev_stroke(0.0), None);
        // Standing exactly on a stroke's landing point moves on
        let on = r.next_stroke(0.0).unwrap();
        assert!((r.next_stroke(on).unwrap() - 1.6).abs() < 1e-6);
        assert_eq!(r.prev_stroke(on), None);
        let on = Replay::stroke_seek_time(&r.strokes()[1]);
        assert_eq!(r.prev_stroke(on).map(|v| (v * 10.0).round()), Some(6.0));
        assert_eq!(r.next_stroke(on), Some(3.0));
        // After the last / before the first
        assert_eq!(r.next_stroke(3.0), None);
        assert_eq!(r.next_stroke(9.0), None);
        assert_eq!(r.prev_stroke(9.0), Some(3.0));
        // No strokes at all
        let none = Replay::from_parts(60.0, 1, 1, vec![[0.0; 4]; JOINTS], None);
        assert_eq!((none.next_stroke(0.0), none.prev_stroke(1.0)), (None, None));
    }

    #[test]
    fn swings_track_parses_and_foreign_column_counts_are_ignored() {
        let rows: Vec<f32> = (0..2 * STROKE_FIELDS).map(|i| i as f32).collect();
        let mk = |cols: usize| {
            let tracks = format!(
                r#"{},{{"name":"swings","dtype":"f32","shape":[2,{cols}],"offset":{}}}"#,
                JOINTS_TRACK.replace("OFF", "0"),
                (2 * 17 * 4 * 4usize).next_multiple_of(16)
            );
            let mut data = blob();
            data.resize(data.len().next_multiple_of(16), 0);
            data.extend(rows.iter().flat_map(|v| v.to_le_bytes()));
            Replay::parse(&build(&header(&tracks, 1), &data)).unwrap()
        };
        let r = mk(STROKE_FIELDS);
        assert_eq!(r.strokes().len(), 2);
        assert_eq!(r.strokes()[1][0], STROKE_FIELDS as f32);
        assert!(mk(STROKE_FIELDS + 1).strokes().is_empty());
    }

    #[test]
    fn rejects_bad_racquet_layout() {
        let racq = r#"{"name":"racquet","dtype":"f32","shape":[2,1,8],"offset":0}"#;
        let tracks = format!("{},{racq}", JOINTS_TRACK.replace("OFF", "0"));
        assert!(Replay::parse(&build(&header(&tracks, 1), &blob())).is_err());
    }

    fn shuttle_row(x: f32, conf: f32, flight: f32) -> [f32; SHUTTLE_FIELDS] {
        [x, 1.0, 0.0, 10.0 * x, 0.0, 0.0, conf, flight]
    }

    fn shuttle_replay(rows: &[[f32; SHUTTLE_FIELDS]]) -> Replay {
        let n = rows.len();
        Replay::from_parts(60.0, n, 1, vec![[0.0; 4]; n * JOINTS], None)
            .with_shuttle(rows.to_vec(), vec![])
    }

    #[test]
    fn parses_shuttle_and_events_and_events_do_not_set_frame_count() {
        // 2 frames of joints3d, a 2x8 shuttle track and a 3x6 events track listed first
        let tracks = format!(
            r#"{{"name":"events","dtype":"f32","shape":[3,6],"offset":0,"per_frame":false}},{{"name":"shuttle","dtype":"f32","shape":[2,8],"offset":80}},{}"#,
            JOINTS_TRACK.replace("OFF", "144")
        );
        let mut data: Vec<u8> = (0..18).flat_map(|i| (i as f32).to_le_bytes()).collect();
        data.resize(80, 0);
        data.extend((0..16).flat_map(|i| (100.0 + i as f32).to_le_bytes()));
        data.extend(blob());
        let r = Replay::parse(&build(&header(&tracks, 1), &data)).unwrap();
        assert_eq!(r.frame_count, 2);
        assert_eq!(r.events().len(), 3);
        assert_eq!(r.events()[2], [12.0, 13.0, 14.0, 15.0, 16.0, 17.0]);
        assert_eq!(r.shuttle(1).unwrap()[0], 108.0);
        // Wrong shuttle width is rejected
        let bad = tracks.replace("[2,8]", "[2,7]");
        assert!(Replay::parse(&build(&header(&bad, 1), &data)).is_err());
    }

    #[test]
    fn score_follows_point_events() {
        let pt = |t: f32, w: f32, a: f32, b: f32| [t, EVENT_POINT, w, a, b, 0.0];
        let ev = [
            [0.5, EVENT_CONTACT, 0.0, 0.0, 1.0, 0.0],
            pt(2.0, 1.0, 1.0, 0.0),
            pt(5.0, 0.0, 1.0, 1.0),
            pt(8.0, 0.0, 2.0, 1.0),
        ];
        assert_eq!(score_in(&ev, 1.0), Some([0, 0]));
        assert_eq!(score_in(&ev, 2.0), Some([0, 1]));
        assert_eq!(score_in(&ev, 6.0), Some([1, 1]));
        assert_eq!(score_in(&ev, 9.0), Some([2, 1]));
        assert_eq!(score_in(&ev[..1], 9.0), None);
    }

    #[test]
    fn sample_shuttle_never_interpolates_across_flights() {
        let r = shuttle_replay(&[
            shuttle_row(0.0, 1.0, 0.0),
            shuttle_row(2.0, 1.0, 0.0),
            shuttle_row(100.0, 1.0, 1.0),
            shuttle_row(102.0, 0.5, 1.0),
            shuttle_row(0.0, 0.0, -1.0),
        ]);
        // Inside flight 0 and flight 1: linear
        let (p, v, c) = r.sample_shuttle(0.5 / 60.0).unwrap();
        assert!((p.x - 1.0).abs() < 1e-6 && (v.x - 10.0).abs() < 1e-6 && c == 1.0);
        let (p, _, c) = r.sample_shuttle(2.5 / 60.0).unwrap();
        assert!((p.x - 101.0).abs() < 1e-5 && c == 0.5);
        // Between the last frame of flight 0 and the first of flight 1: the nearer frame, never a blend
        assert_eq!(r.sample_shuttle(1.25 / 60.0).unwrap().0.x, 2.0);
        assert_eq!(r.sample_shuttle(1.75 / 60.0).unwrap().0.x, 100.0);
        // Next to a conf 0 frame: nearer wins, hidden when that one is missing
        assert_eq!(r.sample_shuttle(3.25 / 60.0).unwrap().0.x, 102.0);
        assert!(r.sample_shuttle(3.75 / 60.0).is_none());
        // Without the track there is nothing to draw
        let none = Replay::from_parts(60.0, 1, 1, vec![[0.0; 4]; JOINTS], None);
        assert!(!none.has_shuttle() && none.sample_shuttle(0.0).is_none());
    }

    #[test]
    fn trail_covers_the_window_within_one_flight() {
        // 60 frames in flight 0 (x = frame), then 10 in flight 1; conf 0.5 for frames 40..45
        let mut rows: Vec<_> = (0..60)
            .map(|f| shuttle_row(f as f32, if (40..45).contains(&f) { 0.5 } else { 1.0 }, 0.0))
            .collect();
        rows.extend((0..10).map(|f| shuttle_row(1000.0 + f as f32, 1.0, 1.0)));
        let r = shuttle_replay(&rows);
        let mut pts = Vec::new();
        // At the exact time of frame 50: the current sample plus frames 49..=15 (age <= 0.6 s = 36 frames: 14 excluded)
        r.shuttle_trail(50.0 / 60.0, 0.6, &mut pts);
        assert_eq!(pts[0].position[0], 50.0);
        assert_eq!(pts[0].age, 0.0);
        assert_eq!(pts.len(), 1 + 36);
        assert!(pts.windows(2).all(|w| w[1].age > w[0].age));
        assert!((pts.last().unwrap().age - 0.6).abs() < 1e-5);
        assert_eq!(pts.iter().filter(|p| p.conf == 0.5).count(), 5);
        // Just after the contact the trail restarts: only the new flight's frames
        r.shuttle_trail(62.0 / 60.0, 0.6, &mut pts);
        assert_eq!(pts.len(), 1 + 2);
        assert!(pts.iter().all(|p| p.position[0] >= 1000.0));
        // Hidden: empty
        let hidden = shuttle_replay(&[shuttle_row(0.0, 0.0, -1.0); 3]);
        hidden.shuttle_trail(0.0, 0.6, &mut pts);
        assert!(pts.is_empty());
    }

    // Startup cost: run with `cargo test --release bench_parse -- --nocapture`
    #[test]
    fn bench_parse() {
        let start = web_time::Instant::now();
        for _ in 0..20 {
            std::hint::black_box(Replay::parse(SAMPLE).unwrap());
        }
        println!("Replay::parse: {:?} per call", start.elapsed() / 20);
    }
}
