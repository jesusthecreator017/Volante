//! A collection protocol: warm-up blocks, then the class x side blocks in a per-session random order, then the finale.

use serde::{Deserialize, Serialize};

/// Labels a block may carry: the 11 classes of the design spec (section 3), plus the two unlabelled kinds
pub const LABELS: [&str; 13] = [
    "not_a_hit", "serve_short", "serve_long", "clear", "smash", "drop", "drive", "lift", "net_shot", "block_push", "net_kill",
    "warmup", "free",
];
/// Labels that have no forehand/backhand side
const SIDELESS: [&str; 3] = ["not_a_hit", "warmup", "free"];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Block {
    pub label: String,
    /// "FH" / "BH", relative to the racquet face and the hitting hand (docs/strokes/README.md); None for sideless labels
    #[serde(default)]
    pub side: Option<String>,
    /// Reps to do; 0 for a timed block
    #[serde(default)]
    pub target: u32,
    /// Timed block: its length in seconds
    #[serde(default)]
    pub secs: Option<u32>,
    pub instruction: String,
    /// Shown as "(optional)": the operator may skip it
    #[serde(default)]
    pub optional: bool,
    /// Live counter threshold in dps for this block; 0 = counter off; None = the protocol's default.
    /// Starting guesses to tune in the pilot.
    #[serde(default)]
    pub count_threshold_dps: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Protocol {
    pub name: String,
    pub version: u32,
    /// Default live counter threshold (dps) for blocks without their own; None = `swingdet::THRESHOLD_DPS`
    #[serde(default)]
    pub count_threshold_dps: Option<f64>,
    #[serde(default)]
    pub warmup: Vec<Block>,
    /// Shuffled per session
    pub blocks: Vec<Block>,
    #[serde(default)]
    pub finale: Vec<Block>,
}

/// The protocols shipped in tools/strokes/protocols/, compiled in so `collect` runs from any directory
pub fn builtin(name: &str) -> Option<&'static str> {
    match name {
        "drills-v1" => Some(include_str!("../../../../tools/strokes/protocols/drills-v1.json")),
        "smoke" => Some(include_str!("../../../../tools/strokes/protocols/smoke.json")),
        _ => None,
    }
}

impl Protocol {
    pub fn parse(text: &str) -> anyhow::Result<Protocol> {
        let p: Protocol = serde_json::from_str(text)?;
        p.validate()?;
        Ok(p)
    }

    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(!self.blocks.is_empty(), "protocol has no blocks");
        for b in self.warmup.iter().chain(&self.blocks).chain(&self.finale) {
            let who = format!("block {} {:?}", b.label, b.side);
            anyhow::ensure!(LABELS.contains(&b.label.as_str()), "{who}: unknown label");
            match (SIDELESS.contains(&b.label.as_str()), b.side.as_deref()) {
                (true, None) | (false, Some("FH" | "BH")) => {}
                _ => anyhow::bail!("{who}: side must be FH/BH, and absent for not_a_hit/warmup/free"),
            }
            anyhow::ensure!((b.target > 0) != b.secs.is_some_and(|s| s > 0), "{who}: needs either target reps or secs");
            anyhow::ensure!(!b.instruction.trim().is_empty(), "{who}: no instruction");
            anyhow::ensure!(b.count_threshold_dps.is_none_or(|t| t.is_finite() && t >= 0.0), "{who}: bad count_threshold_dps");
        }
        Ok(())
    }

    /// The session's block order: warm-up, the middle blocks shuffled by `seed` (same seed, same order), finale
    pub fn order(&self, seed: u64) -> Vec<Block> {
        let mut mid = self.blocks.clone();
        shuffle(&mut mid, seed);
        let dflt = self.count_threshold_dps;
        let mut all: Vec<Block> = self.warmup.iter().cloned().chain(mid).chain(self.finale.iter().cloned()).collect();
        // Resolve the protocol default so a block carries its own threshold
        all.iter_mut().for_each(|b| b.count_threshold_dps = b.count_threshold_dps.or(dflt));
        all
    }
}

/// SplitMix64 (the Python port in 16.2 must use the same): one 64-bit state, no dependency
fn splitmix(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// Fisher-Yates from the end: for i in (1..n).rev(): j = splitmix() % (i + 1); swap(i, j)
pub fn shuffle<T>(v: &mut [T], seed: u64) {
    let mut st = seed;
    for i in (1..v.len()).rev() {
        v.swap(i, (splitmix(&mut st) % (i as u64 + 1)) as usize);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DRILLS: &str = include_str!("../../../../tools/strokes/protocols/drills-v1.json");

    #[test]
    fn shipped_protocols_parse_and_cover_the_classes() {
        let p = Protocol::parse(DRILLS).unwrap();
        let all = p.order(1);
        assert_eq!(all.first().unwrap().label, "warmup");
        assert_eq!(all.last().unwrap().label, "free");
        for class in &LABELS[..11] {
            assert!(all.iter().any(|b| b.label == *class), "{class} missing");
        }
        // 11 classes x side as in the design spec: smash FH + optional BH, not_a_hit timed
        let sides = |l: &str| all.iter().filter(|b| b.label == l).count();
        assert_eq!((sides("smash"), sides("clear"), sides("not_a_hit")), (2, 2, 1));
        assert!(all.iter().filter(|b| b.label == "smash").any(|b| b.optional && b.side.as_deref() == Some("BH")));
        assert!(all.iter().any(|b| b.label == "not_a_hit" && b.secs == Some(60)));
        assert!(all.iter().filter(|b| b.target > 0).all(|b| b.target == if b.label.starts_with("serve") { 15 } else { 25 }));
        let smoke = Protocol::parse(builtin("smoke").unwrap()).unwrap().order(0);
        assert_eq!(smoke.len(), 2);
        let thr = |l: &str| all.iter().find(|b| b.label == l).and_then(|b| b.count_threshold_dps);
        assert_eq!((thr("smash"), thr("drop"), thr("net_shot"), thr("not_a_hit"), thr("free")), (Some(700.0), Some(300.0), Some(200.0), Some(0.0), Some(400.0)));
        assert!(smoke.iter().all(|b| b.target == 3));
    }

    #[test]
    fn shuffle_is_deterministic_and_keeps_the_bookends() {
        let p = Protocol::parse(DRILLS).unwrap();
        let (a, b, c) = (p.order(7), p.order(7), p.order(8));
        assert_eq!(a, b);
        assert_ne!(a, c);
        assert_eq!(a.len(), p.warmup.len() + p.blocks.len() + p.finale.len());
        // Pinned for the Python port (16.2): seed 1 over 0..8
        let mut v: Vec<u32> = (0..8).collect();
        shuffle(&mut v, 1);
        assert_eq!(v, PINNED);
    }
    const PINNED: [u32; 8] = [4, 3, 2, 7, 5, 6, 0, 1];

    #[test]
    fn block_threshold_overrides_protocol_default() {
        let t = r#"{"name":"x","version":1,"count_threshold_dps":500,"blocks":[
            {"label":"clear","side":"FH","target":5,"instruction":"a","count_threshold_dps":700},
            {"label":"drop","side":"FH","target":5,"instruction":"b"}]}"#;
        let o = Protocol::parse(t).unwrap().order(0);
        let mut v: Vec<_> = o.iter().map(|b| (b.label.as_str(), b.count_threshold_dps)).collect();
        v.sort_by(|a, b| a.0.cmp(b.0));
        assert_eq!(v, [("clear", Some(700.0)), ("drop", Some(500.0))]);
        assert!(Protocol::parse(&t.replace("700", "-1")).is_err());
    }

    #[test]
    fn invalid_protocols_are_rejected() {
        let bad = |s: &str| Protocol::parse(&format!(r#"{{"name":"x","version":1,"blocks":[{s}]}}"#)).is_err();
        let ok = r#"{"label":"clear","side":"FH","target":5,"instruction":"hit"}"#;
        assert!(!bad(ok));
        assert!(bad(r#"{"label":"clear","target":5,"instruction":"hit"}"#), "side missing");
        assert!(bad(r#"{"label":"clear","side":"left","target":5,"instruction":"hit"}"#));
        assert!(bad(r#"{"label":"volley","side":"FH","target":5,"instruction":"hit"}"#));
        assert!(bad(r#"{"label":"clear","side":"FH","instruction":"hit"}"#), "no target or secs");
        assert!(bad(r#"{"label":"clear","side":"FH","target":5,"secs":9,"instruction":"hit"}"#), "both");
        assert!(bad(r#"{"label":"not_a_hit","side":"FH","secs":60,"instruction":"x"}"#));
        assert!(Protocol::parse(r#"{"name":"x","version":1,"blocks":[]}"#).is_err());
    }
}
