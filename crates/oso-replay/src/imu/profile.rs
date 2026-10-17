//! Volante Twins style profile `volante-style/1` (arena/contract/STYLE_PROFILE.md) from racquet-pod recordings: `oso-rec profile`.
//! docs/twins-app.md hands the same algorithm to the phone app; arena/kit/oso_arena/style.py holds the validator twin of `validate`.
//!
//! Algorithm (constants below are the contract; change them in the docs too):
//! 1. Samples are rotated into the racquet frame by the session's mount.
//! 2. Swings: `SwingCounter` (swingdet) with a 300 dps threshold, so soft strokes count (the guided-collection default is 400).
//!    One swing per rising edge; the next can start 0.8 s later at the earliest.
//! 3. Head speed of a swing: the peak, over the 0.8 s window from its onset, of |omega perpendicular to the shaft|
//!    (= hypot(wx, wz) in the racquet frame) x 0.53 m (head centre) x 3.6 = km/h. A lower bound when the gyro clips (+-2000 dps per
//!    axis). The gyro bias is not removed (it is a few dps against swings of hundreds). `clipped_frac` = swings whose window holds a
//!    saturated sample / swings.
//! 4. Tempo: the time between consecutive swing onsets of one recording part; a gap over 6 s ends the rally and is dropped.
//!    p25 / median / p75 of the rest.
//! 5. Percentiles use linear interpolation between order statistics (numpy's default); cv = population std / mean of the speeds.
//!    Every number in the file is rounded to 3 significant digits; keys are in declaration order, so the same sessions give
//!    byte-identical output (given `created_at`).

use super::mount::Mount;
use super::packet::{self, Sample};
use super::pose::HEAD_CENTER;
use super::swingdet::SwingCounter;
use crate::collect::session::Session;
use serde::Serialize;
use serde_json::Value;
use std::path::Path;

pub const SCHEMA: &str = "volante-style/1";
pub const THRESHOLD_DPS: f64 = 300.0;
/// Window after a swing's onset in which the peak speed is taken (= the swing counter's refractory time)
pub const WINDOW_S: f64 = 0.8;
/// A longer gap between swings ends the rally
pub const RALLY_SPLIT_S: f64 = 6.0;
pub const MAX_BYTES: usize = 8192;
const MAX_COUNT: i64 = 10_000_000;
const NOTES: &str = "speeds are estimates (gyro x 0.53 m) and lower bounds when clipped";

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Swing {
    /// Onset on the part's device clock, s
    pub t: f64,
    pub kmh: f64,
    pub clipped: bool,
}

/// Swings of one recording, samples already in the racquet frame
pub fn swings(samples: &[Sample]) -> Vec<Swing> {
    let mut counter = SwingCounter::new(THRESHOLD_DPS);
    let onsets: Vec<usize> = (0..samples.len()).filter(|&i| counter.push(&samples[i])).collect();
    onsets
        .into_iter()
        .map(|i| {
            let t = samples[i].t_dev;
            let win = samples[i..].iter().take_while(|s| s.t_dev < t + WINDOW_S);
            let (mut peak, mut clipped) = (0.0f64, false);
            for s in win {
                peak = peak.max(f64::from(s.gyro[0]).hypot(f64::from(s.gyro[2])));
                clipped |= s.saturated;
            }
            Swing { t, kmh: peak * HEAD_CENTER * 3.6, clipped }
        })
        .collect()
}

/// One session: its recording parts in order (each on its own device clock) and the pod firmware, when known
pub struct Input {
    pub fw: Option<String>,
    pub parts: Vec<Vec<Sample>>,
}

fn read_imu(path: &Path, mount: &Mount) -> anyhow::Result<Vec<Sample>> {
    let bytes = std::fs::read(path).map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    let mut samples = packet::parse(&bytes).map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?.samples;
    mount.apply_all(&mut samples);
    Ok(samples)
}

/// A session folder (session.json + parts, the session's own mount) or a bare imu.bin (the given mount)
pub fn load(path: &Path, mount: &Mount) -> anyhow::Result<Input> {
    if !path.is_dir() {
        return Ok(Input { fw: None, parts: vec![read_imu(path, mount)?] });
    }
    let file = path.join("session.json");
    let s = Session::from_json(&std::fs::read_to_string(&file).map_err(|e| anyhow::anyhow!("{}: {e}", file.display()))?)?;
    let m = Mount::from_axes(s.mount.shaft, s.mount.normal)?;
    // schema /1 sessions are one imu.bin
    let mut parts: Vec<(u32, String)> = s.parts.iter().map(|p| (p.index, p.file.clone())).collect();
    if parts.is_empty() {
        parts.push((1, "imu.bin".into()));
    }
    parts.sort();
    let parts = parts
        .iter()
        .map(|(_, f)| {
            anyhow::ensure!(Path::new(f).file_name().is_some_and(|n| n == f.as_str()), "part file {f:?} is not a plain file name");
            read_imu(&path.join(f), &m)
        })
        .collect::<anyhow::Result<_>>()?;
    Ok(Input { fw: Some(s.pod.fw), parts })
}

/// 3 significant digits
pub fn sig3(x: f64) -> f64 {
    if x == 0.0 || !x.is_finite() {
        return x;
    }
    let p = 2 - x.abs().log10().floor() as i32;
    let k = 10f64.powi(p.abs());
    if p >= 0 { (x * k).round() / k } else { (x / k).round() * k }
}

/// Linear-interpolation percentile (q in 0..=1) of a sorted, non-empty slice
fn percentile(sorted: &[f64], q: f64) -> f64 {
    let pos = q * (sorted.len() - 1) as f64;
    let (lo, hi) = (pos.floor() as usize, pos.ceil() as usize);
    sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo as f64)
}

fn sorted(mut v: Vec<f64>) -> Vec<f64> {
    v.sort_by(f64::total_cmp);
    v
}

#[derive(Debug, Serialize)]
pub struct Source {
    pub sessions: usize,
    pub swings: usize,
    pub minutes: f64,
    pub pod_fw: String,
    pub tool: String,
}

#[derive(Debug, Serialize)]
pub struct Speed {
    pub p50_kmh: f64,
    pub p90_kmh: f64,
    pub cv: f64,
    pub clipped_frac: f64,
}

#[derive(Debug, Serialize)]
pub struct Tempo {
    pub median_gap_s: f64,
    pub p25_gap_s: f64,
    pub p75_gap_s: f64,
}

#[derive(Debug, Serialize)]
pub struct Profile {
    pub schema: &'static str,
    pub created_at: String,
    pub source: Source,
    pub speed: Speed,
    pub tempo: Tempo,
    /// null until the stroke classifier exists (Phase 18b)
    pub stroke_mix: Option<Value>,
    pub side_mix: Option<Value>,
    pub notes: &'static str,
}

impl Profile {
    /// Pretty JSON, a trailing newline
    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(self).unwrap_or_default() + "\n"
    }
}

/// `created_at` like 2026-10-17T18:00:00Z, `tool` like "oso-rec 0.1.0". Err when there is nothing to profile.
pub fn compute(inputs: &[Input], created_at: &str, tool: &str) -> anyhow::Result<Profile> {
    let (mut sw, mut gaps, mut minutes) = (Vec::new(), Vec::new(), 0.0);
    for part in inputs.iter().flat_map(|i| &i.parts) {
        if let (Some(a), Some(b)) = (part.first(), part.last()) {
            minutes += (b.t_dev - a.t_dev) / 60.0;
        }
        let s = swings(part);
        gaps.extend(s.windows(2).map(|w| w[1].t - w[0].t).filter(|g| *g <= RALLY_SPLIT_S));
        sw.extend(s);
    }
    anyhow::ensure!(!sw.is_empty(), "no swings found (a swing is a gyro rise through {THRESHOLD_DPS} dps): wrong file or mount?");
    anyhow::ensure!(!gaps.is_empty(), "no two swings within {RALLY_SPLIT_S} s of each other: not enough play to measure a tempo");
    let speeds = sorted(sw.iter().map(|s| s.kmh).collect());
    let n = speeds.len() as f64;
    let mean = speeds.iter().sum::<f64>() / n;
    let std = (speeds.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n).sqrt();
    let gaps = sorted(gaps);
    let mut fws: Vec<String> = inputs
        .iter()
        .filter_map(|i| i.fw.as_deref())
        .map(|f| f.chars().take(32).map(|c| if c.is_ascii_alphanumeric() || ".-_".contains(c) { c } else { '_' }).collect::<String>())
        .filter(|f| !f.is_empty())
        .collect();
    fws.sort();
    fws.dedup();
    let p = Profile {
        schema: SCHEMA,
        created_at: created_at.into(),
        source: Source {
            sessions: inputs.len(),
            swings: sw.len(),
            minutes: sig3(minutes),
            pod_fw: if fws.is_empty() { "unknown".into() } else { fws.join(",") },
            tool: tool.into(),
        },
        speed: Speed {
            p50_kmh: sig3(percentile(&speeds, 0.5)),
            p90_kmh: sig3(percentile(&speeds, 0.9)),
            cv: sig3(if mean > 0.0 { std / mean } else { 0.0 }),
            clipped_frac: sig3(sw.iter().filter(|s| s.clipped).count() as f64 / n),
        },
        tempo: Tempo {
            median_gap_s: sig3(percentile(&gaps, 0.5)),
            p25_gap_s: sig3(percentile(&gaps, 0.25)),
            p75_gap_s: sig3(percentile(&gaps, 0.75)),
        },
        stroke_mix: None,
        side_mix: None,
        notes: NOTES,
    };
    let errs = validate(&serde_json::to_value(&p)?);
    anyhow::ensure!(errs.is_empty(), "the profile does not validate: {}", errs.join("; "));
    Ok(p)
}

fn num(v: &Value, lo: f64, hi: f64) -> bool {
    v.as_f64().is_some_and(|x| x.is_finite() && (lo..=hi).contains(&x))
}

fn int(v: &Value, lo: i64, hi: i64) -> bool {
    (v.is_i64() || v.is_u64()) && v.as_i64().is_some_and(|x| (lo..=hi).contains(&x))
}

/// ascii pattern check for `[A-Za-z0-9._ -]{1,max}`
fn ident(v: &Value, max: usize) -> bool {
    v.as_str().is_some_and(|s| (1..=max).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_alphanumeric() || b" ._-".contains(&b)))
}

fn iso(v: &Value) -> bool {
    v.as_str().is_some_and(|s| {
        let b = s.as_bytes();
        b.len() == 20 && b.iter().enumerate().all(|(i, &c)| match i {
            4 | 7 => c == b'-',
            10 => c == b'T',
            13 | 16 => c == b':',
            19 => c == b'Z',
            _ => c.is_ascii_digit(),
        })
    })
}

fn mix(p: &Value, key: &str, keys: Option<&[&str]>, errs: &mut Vec<String>) {
    let Some(m) = p.get(key).filter(|m| !m.is_null()) else { return };
    let ok = m.as_object().is_some_and(|o| {
        !o.is_empty()
            && o.len() <= 16
            && o.keys().all(|k| (1..=24).contains(&k.len()) && k.bytes().all(|b| b.is_ascii_lowercase() || b == b'_'))
            && keys.is_none_or(|ks| o.len() == ks.len() && ks.iter().all(|k| o.contains_key(*k)))
            && o.values().all(|v| num(v, 0.0, 1.0))
    });
    if !ok {
        errs.push(format!("{key} must be null or an object of class -> share in [0, 1]"));
    } else if (m.as_object().unwrap().values().filter_map(Value::as_f64).sum::<f64>() - 1.0).abs() > 1e-3 {
        errs.push(format!("{key} must sum to 1 (+-1e-3)"));
    }
}

/// Section of a profile that must hold exactly `keys`
fn section<'a>(p: &'a Value, key: &str, keys: &[&str], errs: &mut Vec<String>) -> Option<&'a Value> {
    let o = p.get(key)?;
    if o.as_object().is_some_and(|m| m.len() == keys.len() && keys.iter().all(|k| m.contains_key(*k))) {
        Some(o)
    } else {
        errs.push(format!("{key} keys must be exactly {keys:?}"));
        None
    }
}

/// The rules of STYLE_PROFILE.md, the same as `oso_arena.style.validate_profile` (error strings; empty = valid). The 8 KB limit is
/// on the file (`MAX_BYTES`), not checked here.
pub fn validate(p: &Value) -> Vec<String> {
    let Some(o) = p.as_object() else { return vec!["profile must be a JSON object".into()] };
    const TOP: [&str; 8] = ["schema", "created_at", "source", "speed", "tempo", "stroke_mix", "side_mix", "notes"];
    let mut errs: Vec<String> = o.keys().filter(|k| !TOP.contains(&k.as_str())).map(|k| format!("unknown key {k:?}")).collect();
    errs.extend(TOP[..5].iter().filter(|k| !o.contains_key(**k)).map(|k| format!("missing key {k:?}")));
    if o.get("schema").is_some_and(|s| s != SCHEMA) {
        errs.push(format!("schema must be {SCHEMA:?}"));
    }
    if o.get("created_at").is_some_and(|v| !iso(v)) {
        errs.push("created_at must look like 2026-10-17T18:00:00Z".into());
    }
    if let Some(s) = section(p, "source", &["sessions", "swings", "minutes", "pod_fw", "tool"], &mut errs) {
        for k in ["sessions", "swings"] {
            if !int(&s[k], 1, MAX_COUNT) {
                errs.push(format!("source.{k} must be an integer 1-{MAX_COUNT}"));
            }
        }
        if !num(&s["minutes"], 0.0, MAX_COUNT as f64) || s["minutes"].as_f64() == Some(0.0) {
            errs.push("source.minutes must be above 0".into());
        }
        for k in ["pod_fw", "tool"] {
            if !ident(&s[k], 64) {
                errs.push(format!("source.{k} must be 1-64 characters of letters, digits, space . _ -"));
            }
        }
    }
    let n0 = errs.len();
    if let Some(s) = section(p, "speed", &["p50_kmh", "p90_kmh", "cv", "clipped_frac"], &mut errs) {
        for (k, hi) in [("p50_kmh", 400.0), ("p90_kmh", 400.0), ("cv", 2.0), ("clipped_frac", 1.0)] {
            if !num(&s[k], 0.0, hi) {
                errs.push(format!("speed.{k} must be a number 0-{hi}"));
            }
        }
        if errs.len() == n0 && s["p50_kmh"].as_f64() > s["p90_kmh"].as_f64() {
            errs.push("speed.p50_kmh must not exceed speed.p90_kmh".into());
        }
    }
    let n0 = errs.len();
    if let Some(t) = section(p, "tempo", &["median_gap_s", "p25_gap_s", "p75_gap_s"], &mut errs) {
        for k in ["median_gap_s", "p25_gap_s", "p75_gap_s"] {
            if !num(&t[k], 0.2, 10.0) {
                errs.push(format!("tempo.{k} must be a number 0.2-10 s"));
            }
        }
        if errs.len() == n0 && !(t["p25_gap_s"].as_f64() <= t["median_gap_s"].as_f64() && t["median_gap_s"].as_f64() <= t["p75_gap_s"].as_f64()) {
            errs.push("tempo must satisfy p25_gap_s <= median_gap_s <= p75_gap_s".into());
        }
    }
    mix(p, "stroke_mix", None, &mut errs);
    mix(p, "side_mix", Some(&["fh", "bh"]), &mut errs);
    if let Some(n) = o.get("notes").filter(|n| !n.is_null())
        && !n.as_str().is_some_and(|s| s.len() <= 280 && s.bytes().all(|b| (b' '..=b'~').contains(&b)))
    {
        errs.push("notes must be null or printable ASCII of at most 280 characters".into());
    }
    errs
}

/// Text -> validated JSON value (size, syntax, rules)
pub fn parse(text: &str) -> anyhow::Result<Value> {
    anyhow::ensure!(text.len() <= MAX_BYTES, "profile is over the {MAX_BYTES}-byte limit");
    let v: Value = serde_json::from_str(text)?;
    let errs = validate(&v);
    anyhow::ensure!(errs.is_empty(), "invalid profile: {}", errs.join("; "));
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// 500 Hz stream of `secs`, still but for 100 ms half-sine bursts (start, peak dps about the racquet X axis); the first sample of
    /// a burst's window can be marked saturated
    fn synth(secs: f64, bursts: &[(f64, f64)]) -> Vec<Sample> {
        (0..(secs * 500.0) as usize)
            .map(|i| {
                let t = i as f64 / 500.0;
                let w: f64 = bursts
                    .iter()
                    .filter(|b| (b.0..b.0 + 0.1).contains(&t))
                    .map(|b| b.1 * (std::f64::consts::PI * (t - b.0) / 0.1).sin())
                    .sum();
                Sample { t_dev: t, acc: [0.0, 0.0, 9.8], gyro: [w.to_radians() as f32, 0.0, 0.0], saturated: w.abs() > 1999.0 }
            })
            .collect()
    }

    fn input(parts: Vec<Vec<Sample>>) -> Input {
        Input { fw: Some("0.1".into()), parts }
    }

    #[test]
    fn speed_is_the_perpendicular_peak_times_the_head_centre() {
        // 1000 dps about X (perpendicular to the shaft Y): 17.45 rad/s * 0.53 * 3.6 = 33.3 km/h
        let s = swings(&synth(4.0, &[(1.0, 1000.0)]));
        assert_eq!(s.len(), 1);
        assert!((s[0].kmh - 1000f64.to_radians() * 0.53 * 3.6).abs() < 0.2, "{}", s[0].kmh);
        assert!(!s[0].clipped);
        // about the shaft (Y): spins the racquet without moving the head
        let mut y = synth(4.0, &[(1.0, 1000.0)]);
        y.iter_mut().for_each(|s| s.gyro = [0.0, s.gyro[0], 0.0]);
        assert!(swings(&y)[0].kmh < 1.0);
        // a soft stroke (350 dps) counts, a waggle (250) does not
        assert_eq!(swings(&synth(4.0, &[(1.0, 350.0)])).len(), 1);
        assert_eq!(swings(&synth(4.0, &[(1.0, 250.0)])).len(), 0);
    }

    #[test]
    fn tempo_drops_gaps_over_six_seconds_and_never_spans_parts() {
        // swings at 1, 3, 5 (gaps 2, 2), 12 (gap 7: split), 14 (2)
        let b = [(1.0, 900.0), (3.0, 900.0), (5.0, 900.0), (12.0, 900.0), (14.0, 900.0)];
        let p = compute(&[input(vec![synth(16.0, &b)])], "2026-10-17T18:00:00Z", "oso-rec test").unwrap();
        assert_eq!((p.source.swings, p.tempo.median_gap_s, p.tempo.p25_gap_s, p.tempo.p75_gap_s), (5, 2.0, 2.0, 2.0));
        // the same swings as two parts: the boundary adds no gap
        let two = vec![synth(6.0, &b[..3]), synth(6.0, &[(1.0, 900.0), (3.5, 900.0)])];
        let p = compute(&[input(two)], "2026-10-17T18:00:00Z", "oso-rec test").unwrap();
        assert_eq!((p.source.swings, p.tempo.p25_gap_s, p.tempo.p75_gap_s), (5, 2.0, 2.25));
        assert!((p.source.minutes - 12.0 / 60.0).abs() < 0.001);
        // one swing, or swings that are all far apart: nothing to profile
        assert!(compute(&[input(vec![synth(4.0, &[(1.0, 900.0)])])], "2026-10-17T18:00:00Z", "t").is_err());
        assert!(compute(&[input(vec![synth(20.0, &[(1.0, 900.0), (10.0, 900.0)])])], "2026-10-17T18:00:00Z", "t").is_err());
    }

    #[test]
    fn clipped_fraction_and_percentiles() {
        let b = [(1.0, 600.0), (3.0, 800.0), (5.0, 1000.0), (7.0, 1200.0), (9.0, 2500.0)];
        let p = compute(&[input(vec![synth(11.0, &b)])], "2026-10-17T18:00:00Z", "oso-rec test").unwrap();
        assert_eq!(p.speed.clipped_frac, 0.2);
        let v = |d: f64| d.to_radians() * 0.53 * 3.6;
        assert!((p.speed.p50_kmh - sig3(v(1000.0))).abs() < 0.2);
        assert!((percentile(&[1.0, 2.0, 3.0, 4.0], 0.5) - 2.5).abs() < 1e-12 && percentile(&[7.0], 0.9) == 7.0);
    }

    #[test]
    fn three_significant_digits() {
        assert_eq!((sig3(58.1234), sig3(0.123456), sig3(1239.6), sig3(0.0), sig3(2.0)), (58.1, 0.123, 1240.0, 0.0, 2.0));
    }

    #[test]
    fn deterministic_and_valid() {
        let b = [(1.0, 700.0), (3.1, 950.0), (5.3, 1100.0)];
        let make = || compute(&[input(vec![synth(8.0, &b)])], "2026-10-17T18:00:00Z", "oso-rec test").unwrap().to_json();
        let a = make();
        assert_eq!(a, make());
        assert!(parse(&a).is_ok());
        assert!(!a.contains("session") || a.contains("\"sessions\""), "no ids or names");
        let v: Value = serde_json::from_str(&a).unwrap();
        assert!(v["stroke_mix"].is_null() && v["side_mix"].is_null());
    }

    fn good() -> Value {
        json!({
            "schema": "volante-style/1", "created_at": "2026-10-17T18:00:00Z",
            "source": {"sessions": 3, "swings": 1240, "minutes": 52.4, "pod_fw": "0.1", "tool": "oso-rec 0.1.0"},
            "speed": {"p50_kmh": 58.0, "p90_kmh": 84.0, "cv": 0.27, "clipped_frac": 0.31},
            "tempo": {"median_gap_s": 1.9, "p25_gap_s": 1.4, "p75_gap_s": 2.6},
            "stroke_mix": null, "side_mix": {"fh": 0.7, "bh": 0.3}, "notes": "x"
        })
    }

    #[test]
    fn validation_edge_cases() {
        assert!(validate(&good()).is_empty());
        let bad = |f: &dyn Fn(&mut Value)| {
            let mut v = good();
            f(&mut v);
            assert!(!validate(&v).is_empty(), "{v}");
        };
        bad(&|v| v["schema"] = json!("volante-style/2"));
        bad(&|v| v["name"] = json!("Jesus"));
        bad(&|v| v["created_at"] = json!("2026-10-17 18:00"));
        bad(&|v| v["source"]["sessions"] = json!(0));
        bad(&|v| v["source"]["swings"] = json!(10_000_001));
        bad(&|v| v["source"]["swings"] = json!(5.0));
        bad(&|v| v["source"]["minutes"] = json!(0));
        bad(&|v| v["source"]["pod_fw"] = json!("a/b"));
        bad(&|v| v["speed"]["p90_kmh"] = json!(401));
        bad(&|v| v["speed"]["p50_kmh"] = json!(90));
        bad(&|v| v["speed"]["cv"] = json!(2.5));
        bad(&|v| v["speed"]["clipped_frac"] = json!(1.2));
        bad(&|v| v["tempo"]["median_gap_s"] = json!(0.1));
        bad(&|v| v["tempo"]["p75_gap_s"] = json!(10.5));
        bad(&|v| v["tempo"]["p25_gap_s"] = json!(2.0));
        bad(&|v| v["side_mix"] = json!({"fh": 0.7, "bh": 0.2}));
        bad(&|v| v["side_mix"] = json!({"fh": 1.0}));
        bad(&|v| v["stroke_mix"] = json!({"clear": -0.1, "smash": 1.1}));
        bad(&|v| v["speed"] = json!({"p50_kmh": 1}));
        bad(&|v| { v.as_object_mut().unwrap().remove("tempo"); });
        assert!(parse(&"x".repeat(MAX_BYTES + 1)).is_err());
    }

    #[test]
    fn a_session_folder_reads_every_part_with_its_own_mount_and_leaks_no_ids() {
        let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../arena/kit/tests/data/twins_vector.imu.bin");
        let dir = std::env::temp_dir().join(format!("oso-profile-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        for f in ["imu.part1.bin", "imu.part2.bin"] {
            std::fs::copy(&fixture, dir.join(f)).unwrap();
        }
        let part = |i: u32| json!({"index": i, "file": format!("imu.part{i}.bin"), "packets": 1200, "t_first_us": 0, "t_last_us": 1});
        let mut session = json!({
            "schema": "oso-strokes-session/2", "player": {"id": "P07"}, "protocol": {"name": "drills-v1", "version": 1, "seed": 1},
            "pod": {"mac": "AA:BB", "fw": "0.1"}, "mount": {"shaft": [0, 1, 0], "normal": [0, 0, 1]}, "started_at": "2026-10-17T14:03:22Z",
            "parts": [part(2), part(1)], "blocks": [], "markers": [], "operator_notes": "Jesus"
        });
        std::fs::write(dir.join("session.json"), session.to_string()).unwrap();
        // the session's mount is the identity here, so both parts give the vector's swings (the parts are read in index order)
        let p = compute(&[load(&dir, &Mount::FLAT).unwrap()], "2026-10-17T18:00:00Z", "oso-rec 0.1.0").unwrap();
        let one = compute(&[load(&fixture, &Mount::IDENTITY).unwrap()], "2026-10-17T18:00:00Z", "oso-rec 0.1.0").unwrap();
        assert_eq!((p.source.sessions, p.source.swings, p.source.pod_fw.as_str()), (1, 16, "0.1"));
        assert_eq!((p.speed.p50_kmh, p.tempo.median_gap_s), (one.speed.p50_kmh, one.tempo.median_gap_s));
        let text = p.to_json();
        assert!(!text.contains("P07") && !text.contains("AA:BB") && !text.contains("Jesus") && !text.contains("14:03"), "{text}");
        // a part file that is a path is refused
        session["parts"][0]["file"] = json!("../x.bin");
        std::fs::write(dir.join("session.json"), session.to_string()).unwrap();
        assert!(load(&dir, &Mount::FLAT).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn cross_language_vector_and_the_real_recording() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../arena/kit/tests/data");
        // The tiny fixture: Rust output equals the committed JSON byte for byte (tests/test_twins.py recomputes it in Python)
        let inp = load(&dir.join("twins_vector.imu.bin"), &Mount::IDENTITY).unwrap();
        let got = compute(&[inp], "2026-10-17T18:00:00Z", "oso-rec 0.1.0").unwrap().to_json();
        let want = std::fs::read_to_string(dir.join("twins_vector.profile.json")).unwrap();
        assert_eq!(got, want);
        assert!(parse(&want).is_ok());

        // smashes_live1: numbers of the same method in numpy (arena/kit/tests/test_twins.py::reference_profile) within 5 %; the
        // 2026-10-17 analysis of the same recording had mean 68.9 km/h and max 94.6 km/h over its 41 smashes
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let dirs = [std::env::var_os("OSO_HW_DIR").map(Into::into), Some(root.join("out/hw"))];
        let Some(path) = dirs.into_iter().flatten().map(|d: std::path::PathBuf| d.join("smashes_live1.bin")).find(|p| p.exists()) else {
            eprintln!("skipped: out/hw/smashes_live1.bin not found");
            return;
        };
        let p = compute(&[load(&path, &Mount::FLAT).unwrap()], "2026-10-17T18:00:00Z", "t").unwrap();
        let near = |a: f64, b: f64| (a - b).abs() <= 0.05 * b;
        let mut top: Vec<f64> = load(&path, &Mount::FLAT).unwrap().parts.iter().flat_map(|s| swings(s)).map(|s| s.kmh).collect();
        top.sort_by(|a, b| b.total_cmp(a));
        assert!(near(top[0], 94.6), "max {}", top[0]);
        assert!(near(top[..41].iter().sum::<f64>() / 41.0, 68.9), "mean of the 41 hardest swings {}", top[..41].iter().sum::<f64>() / 41.0);
        assert!(near(p.speed.p50_kmh, REAL_P50) && near(p.speed.p90_kmh, REAL_P90), "{} {}", p.speed.p50_kmh, p.speed.p90_kmh);
        assert!(near(p.tempo.median_gap_s, REAL_GAP), "{}", p.tempo.median_gap_s);
    }

    /// Pinned from the numpy reference (tests/test_twins.py::reference_profile) on smashes_live1.bin, flat mount: 63 swings in 4 min
    /// (the analysis counted only the 41 smashes)
    const REAL_P50: f64 = 62.57;
    const REAL_P90: f64 = 82.01;
    const REAL_GAP: f64 = 2.656;
}
