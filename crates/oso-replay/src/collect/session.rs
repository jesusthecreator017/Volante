//! `session.json` of a collected session (schema `oso-strokes-session/1`): who, what protocol, which pod and mount,
//! and where each block lies on the device clock. Times are device `t_us`, the clock of the packets in `imu.bin`, so a
//! block maps onto the recording exactly with no sync step. Only the pseudonymous ID identifies a player.

use super::blocks::Record;
use serde::{Deserialize, Serialize};
use std::str::FromStr;

pub const SCHEMA: &str = "oso-strokes-session/1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Handed {
    R,
    L,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Level {
    Beginner,
    Club,
    Advanced,
}

impl FromStr for Handed {
    type Err = anyhow::Error;
    fn from_str(s: &str) -> anyhow::Result<Self> {
        match s {
            "R" | "r" => Ok(Handed::R),
            "L" | "l" => Ok(Handed::L),
            _ => anyhow::bail!("--handed is R or L"),
        }
    }
}

impl FromStr for Level {
    type Err = anyhow::Error;
    fn from_str(s: &str) -> anyhow::Result<Self> {
        match s {
            "beginner" => Ok(Level::Beginner),
            "club" => Ok(Level::Club),
            "advanced" => Ok(Level::Advanced),
            _ => anyhow::bail!("--level is beginner, club or advanced"),
        }
    }
}

/// `^P\d{2,3}$`
pub fn valid_player_id(id: &str) -> bool {
    id.strip_prefix('P').is_some_and(|d| (2..=3).contains(&d.len()) && d.bytes().all(|b| b.is_ascii_digit()))
}

/// `NNN-NNN`, a 5 cm bin
pub fn valid_height_bin(s: &str) -> bool {
    let num = |t: &str| (t.len() == 3 && t.bytes().all(|b| b.is_ascii_digit())).then(|| t.parse::<u32>().ok()).flatten();
    s.split_once('-').is_some_and(|(a, b)| matches!((num(a), num(b)), (Some(a), Some(b)) if b == a + 5))
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Player {
    pub id: String,
    pub handed: Option<Handed>,
    pub level: Option<Level>,
    pub height_bin: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProtocolRef {
    pub name: String,
    pub version: u32,
    /// Seeds the block order (`Protocol::order`)
    pub seed: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Pod {
    pub mac: String,
    pub fw: String,
}

/// The racquet's shaft and face-normal axes in the sensor frame (`~/.config/oso/mount.json`)
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MountAxes {
    pub shaft: [f64; 3],
    pub normal: [f64; 3],
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MarkerRec {
    pub t_us: i64,
    /// "tap" or "command"
    pub source: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Session {
    pub schema: String,
    pub player: Player,
    pub protocol: ProtocolRef,
    pub pod: Pod,
    pub mount: MountAxes,
    /// UTC, `2026-10-17T14:03:22Z`
    pub started_at: String,
    pub blocks: Vec<Record>,
    pub markers: Vec<MarkerRec>,
    pub operator_notes: String,
}

impl Session {
    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(self).unwrap_or_default() + "\n"
    }

    pub fn from_json(text: &str) -> anyhow::Result<Session> {
        let s: Session = serde_json::from_str(text)?;
        anyhow::ensure!(s.schema == SCHEMA, "unknown session schema {}", s.schema);
        anyhow::ensure!(valid_player_id(&s.player.id), "player id {:?} is not an anonymous ID", s.player.id);
        Ok(s)
    }
}

/// (year, month, day, hour, minute, second) UTC of a Unix time
fn civil(secs: u64) -> (i64, u32, u32, u64, u64, u64) {
    let (days, rem) = ((secs / 86_400) as i64, secs % 86_400);
    // Howard Hinnant's days-from-civil, inverted
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = yoe + era * 400 + i64::from(m <= 2);
    (y, m, d, rem / 3600, rem / 60 % 60, rem % 60)
}

pub fn iso_utc(secs: u64) -> String {
    let (y, mo, d, h, mi, s) = civil(secs);
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}Z")
}

/// `<YYYYMMDD-HHMMSS>-P07`
pub fn folder_name(secs: u64, player: &str) -> String {
    let (y, mo, d, h, mi, s) = civil(secs);
    format!("{y:04}{mo:02}{d:02}-{h:02}{mi:02}{s:02}-{player}")
}

#[cfg(test)]
mod tests {
    use super::super::blocks::Status;
    use super::*;

    fn sample() -> Session {
        Session {
            schema: SCHEMA.into(),
            player: Player { id: "P07".into(), handed: Some(Handed::L), level: Some(Level::Club), height_bin: Some("170-175".into()) },
            protocol: ProtocolRef { name: "drills-v1".into(), version: 1, seed: 123_456_789_012 },
            pod: Pod { mac: "AA:BB:CC:DD:EE:FF".into(), fw: "1.2".into() },
            mount: MountAxes { shaft: [0.0, 0.0, -1.0], normal: [0.0, -1.0, 0.0] },
            started_at: iso_utc(1_792_245_802),
            blocks: vec![
                Record { index: 0, label: "clear".into(), side: Some("FH".into()), target: 25, counted: 24, t_start_us: Some(1_000_000), t_end_us: Some(61_000_000), status: Status::Done, note: None },
                Record { index: 1, label: "drop".into(), side: Some("BH".into()), target: 25, counted: 0, t_start_us: None, t_end_us: None, status: Status::Skipped, note: Some("sore wrist".into()) },
            ],
            markers: vec![MarkerRec { t_us: 5_000_000, source: "tap".into() }],
            operator_notes: String::new(),
        }
    }

    #[test]
    fn session_json_round_trips_and_has_the_documented_shape() {
        let s = sample();
        let text = s.to_json();
        assert_eq!(Session::from_json(&text).unwrap(), s);
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(v["schema"], "oso-strokes-session/1");
        assert_eq!(v["player"]["handed"], "L");
        assert_eq!(v["player"]["level"], "club");
        assert_eq!(v["blocks"][0]["status"], "done");
        assert_eq!(v["blocks"][1]["status"], "skipped");
        assert!(v["blocks"][1]["t_start_us"].is_null());
        assert_eq!(v["pod"]["fw"], "1.2");
        assert_eq!(v["started_at"], "2026-10-17T14:03:22Z");
        // The player object carries the ID and the coarse attributes only
        assert_eq!(v["player"].as_object().unwrap().len(), 4);
    }

    #[test]
    fn ids_must_be_anonymous() {
        for ok in ["P01", "P07", "P123"] {
            assert!(valid_player_id(ok), "{ok}");
        }
        for bad in ["", "P", "P1", "P1234", "p07", "Alice", "P07x", "P0 7", "Jesus", "07", "P-1"] {
            assert!(!valid_player_id(bad), "{bad}");
        }
        let mut s = sample();
        s.player.id = "Alice".into();
        assert!(Session::from_json(&s.to_json()).is_err());
        assert!(valid_height_bin("170-175") && !valid_height_bin("170-180") && !valid_height_bin("17-22") && !valid_height_bin("abc"));
    }

    #[test]
    fn utc_formatting() {
        assert_eq!(iso_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(iso_utc(951_782_400 + 3661), "2000-02-29T01:01:01Z");
        assert_eq!(folder_name(1_792_245_802, "P07"), "20261017-140322-P07");
    }
}
