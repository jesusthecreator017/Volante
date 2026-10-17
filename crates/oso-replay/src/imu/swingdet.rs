//! Streaming swing counter for guided collection (`oso-rec collect`). Pure: samples in, "a swing just started" out.
//!
//! The Python segmentation of Phase 16.2 (`tools/strokes/`) must port exactly this, constants included:
//! 1. `|w|` = gyro vector norm in dps (frame-independent, so no mount is needed), averaged over the trailing
//!    `SMOOTH_S` (20 ms) of samples.
//! 2. Armed: the smoothed value rises through `THRESHOLD_DPS` (400) -> one swing, stamped with that sample's time.
//!    The counter is then disarmed.
//! 3. Re-armed when `REFRACTORY_S` (0.8 s) has passed since the last count AND the smoothed value has dropped below
//!    `STILL_DPS` (150) (the racquet came to rest between reps; a swing's own follow-through cannot count twice).
//!
//! Detection happens on the way up, so the live counter reacts before the swing ends; the peak (for windows) is found
//! offline.

use super::packet::Sample;
use std::collections::VecDeque;

pub const SMOOTH_S: f64 = 0.020;
pub const THRESHOLD_DPS: f64 = 400.0;
pub const REFRACTORY_S: f64 = 0.8;
pub const STILL_DPS: f64 = 150.0;

#[derive(Debug)]
pub struct SwingCounter {
    /// (time, dps) inside the smoothing window, and their sum
    win: VecDeque<(f64, f64)>,
    sum: f64,
    armed: bool,
    last: Option<f64>,
    pub count: u32,
    threshold: f64,
    refractory: f64,
}

impl SwingCounter {
    #[allow(clippy::new_without_default)]
    pub fn new() -> Self {
        Self::with_params(THRESHOLD_DPS, REFRACTORY_S)
    }

    /// Other threshold/refractory (tests comparing against the offline analysis)
    pub fn with_params(threshold: f64, refractory: f64) -> Self {
        Self { win: VecDeque::new(), sum: 0.0, armed: true, last: None, count: 0, threshold, refractory }
    }

    /// One sample in time order. True when it starts a new swing.
    pub fn push(&mut self, s: &Sample) -> bool {
        let dps = s.gyro.iter().map(|&g| f64::from(g).powi(2)).sum::<f64>().sqrt().to_degrees();
        self.win.push_back((s.t_dev, dps));
        self.sum += dps;
        while self.win.front().is_some_and(|w| s.t_dev - w.0 >= SMOOTH_S) {
            self.sum -= self.win.pop_front().map_or(0.0, |w| w.1);
        }
        let v = self.sum / self.win.len() as f64;
        if !self.armed {
            self.armed = v < STILL_DPS && self.last.is_none_or(|t| s.t_dev - t >= self.refractory);
        } else if v >= self.threshold {
            self.armed = false;
            self.last = Some(s.t_dev);
            self.count += 1;
            return true;
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::imu::packet;

    /// Looks for a recording in `$OSO_HW_DIR`, `out/hw` here, or the main checkout's (worktrees do not share `out/`)
    fn hw(name: &str) -> Option<Vec<Sample>> {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let dirs = [std::env::var_os("OSO_HW_DIR").map(Into::into), Some(root.join("out/hw"))];
        let bytes = dirs.into_iter().flatten().find_map(|d: std::path::PathBuf| std::fs::read(d.join(name)).ok());
        if bytes.is_none() {
            eprintln!("skipped: out/hw/{name} not found");
        }
        Some(packet::parse(&bytes?).unwrap().samples)
    }

    fn count(samples: &[Sample]) -> u32 {
        count_with(SwingCounter::new(), samples)
    }

    fn count_with(mut c: SwingCounter, samples: &[Sample]) -> u32 {
        samples.iter().for_each(|s| {
            c.push(s);
        });
        c.count
    }

    /// Stream of `secs` at 500 Hz: still, with `bursts` (start time, peak dps) of 100 ms half-sine swings about X
    fn synth(secs: f64, bursts: &[(f64, f64)]) -> Vec<Sample> {
        (0..(secs * 500.0) as usize)
            .map(|i| {
                let t = i as f64 / 500.0;
                let w = bursts
                    .iter()
                    .filter(|b| (b.0..b.0 + 0.1).contains(&t))
                    .map(|b| b.1 * (std::f64::consts::PI * (t - b.0) / 0.1).sin())
                    .sum::<f64>();
                Sample { t_dev: t, acc: [0.0, 0.0, 9.8], gyro: [w.to_radians() as f32, 0.0, 0.0], saturated: false }
            })
            .collect()
    }

    #[test]
    fn counts_spaced_swings_once_each_and_nothing_when_still() {
        assert_eq!(count(&synth(5.0, &[])), 0);
        // Below the threshold: a waggle
        assert_eq!(count(&synth(5.0, &[(1.0, 300.0)])), 0);
        let b = [(1.0, 1500.0), (2.0, 900.0), (3.5, 600.0)];
        assert_eq!(count(&synth(6.0, &b)), 3);
        // Two bursts 0.3 s apart are one swing (refractory)
        assert_eq!(count(&synth(4.0, &[(1.0, 1200.0), (1.3, 1200.0)])), 1);
        // 1 s apart are two
        assert_eq!(count(&synth(4.0, &[(1.0, 1200.0), (2.0, 1200.0)])), 2);
    }

    #[test]
    fn real_smash_recordings_count_near_the_offline_analysis() {
        // The 2026-10-17 analysis found 41 and 18 smashes: peaks > 700 dps with a 1 s refractory. The live counter's
        // 400 dps threshold also takes the softer swings in between, so it counts more; the analysis' own definition
        // must reproduce its numbers within 1, and the live count must not be lower.
        for (file, found) in [("smashes_live1.bin", 41u32), ("smashes.bin", 18)] {
            let Some(s) = hw(file) else { continue };
            let strict = count_with(SwingCounter::with_params(700.0, 1.0), &s);
            let live = count(&s);
            eprintln!("{file}: analysis {found}, 700 dps/1 s {strict}, live (400 dps/0.8 s) {live}");
            assert!(strict.abs_diff(found) <= 1, "{file}: {strict} vs {found}");
            assert!(live >= strict, "{file}");
        }
    }

    #[test]
    fn real_still_stretches_count_nothing() {
        // still3.bin is a still desk capture (still.bin is the byte-misaligned one: garbage); also the quietest 5 s of a smash session
        if let Some(s) = hw("still3.bin") {
            assert_eq!(count(&s), 0);
        }
        if let Some(s) = hw("smashes.bin") {
            let dps = |s: &Sample| s.gyro.iter().map(|&g| f64::from(g).powi(2)).sum::<f64>().sqrt().to_degrees();
            // 5 s windows, stride 1 s: the one with the lowest peak
            let best = (0..s.len().saturating_sub(2500)).step_by(500).min_by(|&a, &b| {
                let m = |i: usize| s[i..i + 2500].iter().map(dps).fold(0.0, f64::max);
                m(a).total_cmp(&m(b))
            });
            if let Some(i) = best {
                eprintln!("quietest 5 s starts at {:.0} s", s[i].t_dev - s[0].t_dev);
                assert_eq!(count(&s[i..i + 2500]), 0);
            }
        }
    }
}
