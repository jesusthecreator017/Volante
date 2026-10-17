//! Swing detection from the racquet gyro. Pure: samples in, swings out.
//! The thresholds are the contract of the handed-off BLE app spec (300 dps start, 120 dps for 80 ms end, 80 ms minimum,
//! head center 0.53 m): change them in both places or not at all.

use super::packet::{Gap, Sample};
use super::pose::HEAD_CENTER;
use super::sync;

pub const START_DPS: f64 = 300.0;
pub const END_DPS: f64 = 120.0;
pub const END_HOLD_S: f64 = 0.08;
pub const MIN_DURATION_S: f64 = 0.08;
/// An impact counts when it lands inside the swing window or this long after its end
pub const IMPACT_AFTER_S: f64 = 0.06;
/// Forward swing = the run ending at the peak that stays above this fraction of the peak
const FORWARD_FRACTION: f64 = 0.5;
/// Without an impact: the latest run whose peak reaches this fraction of the window maximum is the forward swing
const AIR_SWING_FRACTION: f64 = 0.75;
/// A packet gap of at least this many missing packets inside a swing halves the confidence
const GAP_PACKETS: u64 = 4;

#[derive(Debug, Clone, PartialEq)]
pub struct Swing {
    /// Video clock, seconds
    pub t_start: f64,
    pub t_peak: f64,
    pub t_end: f64,
    /// Start of the forward swing (backswing is t_start..t_forward)
    pub t_forward: f64,
    /// Follow-through starts at the peak, or at the impact if that is later
    pub t_follow: f64,
    pub peak_dps: f64,
    /// (video time, g) of the strongest racquet impact in the window
    pub impact: Option<(f64, f64)>,
    pub saturated: bool,
    pub confidence: f32,
}

impl Swing {
    pub fn timing_ms(&self) -> Option<f64> {
        self.impact.map(|i| (i.0 - self.t_peak) * 1e3)
    }

    /// Rotation-only head speed at the peak, m/s
    pub fn head_speed_rot(&self) -> f64 {
        self.peak_dps.to_radians() * HEAD_CENTER
    }
}

/// `bias` in rad/s (racquet frame, after the mount rotation); `t_video = t_dev * (1 + a) + b`; `rate_hz`: the capture's sample rate.
/// (GAP_PACKETS counts packets, not seconds, so it is the same at any rate.)
pub fn detect(
    samples: &[Sample],
    gaps: &[Gap],
    bias: [f64; 3],
    a: f64,
    b: f64,
    rate_hz: f64,
) -> Vec<Swing> {
    let dps: Vec<f64> = samples
        .iter()
        .map(|s| {
            (0..3)
                .map(|k| (s.gyro[k] as f64 - bias[k]).powi(2))
                .sum::<f64>()
                .sqrt()
                .to_degrees()
        })
        .collect();
    let strength = sync::spike_strength(samples, rate_hz);
    let impacts = sync::spikes_with_peak(samples, &strength, 0, sync::IMPACT_G, rate_hz);
    let mut out = Vec::new();
    let (mut start, mut below): (Option<usize>, Option<usize>) = (None, None);
    for i in 0..samples.len() {
        match start {
            None => {
                if dps[i] > START_DPS {
                    start = Some(i);
                    below = None;
                }
            }
            Some(s) => {
                if dps[i] >= END_DPS {
                    below = None;
                } else {
                    below.get_or_insert(i);
                }
                // Ended: 80 ms below the end threshold, or the data ran out
                let held = below.is_some_and(|e| samples[i].t_dev - samples[e].t_dev >= END_HOLD_S);
                if held || i + 1 == samples.len() {
                    let e = below.unwrap_or(i);
                    start = None;
                    below = None;
                    if samples[e].t_dev - samples[s].t_dev >= MIN_DURATION_S {
                        out.push(finish(samples, gaps, &dps, &impacts, s, e, a, b));
                    }
                }
            }
        }
    }
    out
}

#[allow(clippy::too_many_arguments)]
fn finish(
    samples: &[Sample],
    gaps: &[Gap],
    dps: &[f64],
    impacts: &[(f64, f64)],
    s: usize,
    e: usize,
    a: f64,
    b: f64,
) -> Swing {
    let clock = |t: f64| t * (1.0 + a) + b;
    let (t0, t1) = (samples[s].t_dev, samples[e].t_dev);
    let impact = impacts
        .iter()
        .filter(|i| i.0 >= t0 && i.0 <= t1 + IMPACT_AFTER_S)
        .max_by(|x, y| x.1.total_cmp(&y.1))
        .copied();
    // The window can hold backswing, forward swing and follow-through in one piece (|w| never dips below 120 dps between
    // them), so the plain maximum can be the backswing. Runs = contiguous regions above half the window maximum.
    // With an impact the forward swing is the run containing it, else the last run ending before it; without one (air
    // swing) it is the latest run whose peak reaches 75 % of the window maximum. A single lobe gives the plain maximum.
    // ponytail: without an impact a follow-through lobe above 75 % of the max (e.g. 900/800/700) is picked over the
    // forward swing; only an impact (or a trained model) can tell them apart.
    let top = (s..=e).map(|k| dps[k]).fold(0.0, f64::max);
    let mut runs: Vec<(usize, usize)> = Vec::new();
    for (k, &v) in dps.iter().enumerate().take(e + 1).skip(s) {
        if v >= FORWARD_FRACTION * top {
            match runs.last_mut() {
                Some(r) if r.1 + 1 == k => r.1 = k,
                _ => runs.push((k, k)),
            }
        }
    }
    let run_max = |r: &(usize, usize)| (r.0..=r.1).map(|k| dps[k]).fold(0.0, f64::max);
    let chosen = match impact {
        Some((ti, _)) => runs
            .iter()
            .find(|r| samples[r.0].t_dev <= ti && ti <= samples[r.1].t_dev)
            .or_else(|| runs.iter().rev().find(|r| samples[r.1].t_dev < ti))
            .or(runs.first()),
        None => runs
            .iter()
            .rev()
            .find(|r| run_max(r) >= AIR_SWING_FRACTION * top),
    }
    .copied()
    .unwrap();
    let run = chosen.0;
    let pk = (chosen.0..=chosen.1)
        .max_by(|&x, &y| dps[x].total_cmp(&dps[y]))
        .unwrap();
    // Parabola through the peak sample and its neighbours (device clock)
    let t_peak = if pk > 0 && pk + 1 < samples.len() {
        let (y0, y1, y2) = (dps[pk - 1], dps[pk], dps[pk + 1]);
        let den = y0 - 2.0 * y1 + y2;
        let (t0, t2) = (samples[pk - 1].t_dev, samples[pk + 1].t_dev);
        if den < -1e-9 {
            let off = (0.5 * (y0 - y2) / den).clamp(-1.0, 1.0);
            samples[pk].t_dev + off * (t2 - t0) / 2.0
        } else {
            samples[pk].t_dev
        }
    } else {
        samples[pk].t_dev
    };
    let fwd = run;
    let saturated = samples[s..=e].iter().any(|x| x.saturated);
    let gap = gaps
        .iter()
        .any(|g| g.missing >= GAP_PACKETS && g.t_end >= t0 && g.t_start <= t1);
    Swing {
        t_start: clock(t0),
        t_peak: clock(t_peak),
        t_end: clock(t1),
        t_forward: clock(samples[fwd].t_dev),
        t_follow: clock(impact.map_or(t_peak, |i| i.0.max(t_peak))),
        peak_dps: dps[pk],
        impact: impact.map(|i| (clock(i.0), i.1)),
        saturated,
        confidence: if saturated || gap { 0.5 } else { 1.0 },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DT: f64 = 0.002;

    /// |omega| profile in dps at 500 Hz (rotation about x), optional acc spike (g) at a sample
    fn samples(profile: &[f64], spike: Option<(usize, f64)>) -> Vec<Sample> {
        profile
            .iter()
            .enumerate()
            .map(|(i, &w)| {
                let mut acc = [0.0, 0.0, 9.80665];
                if let Some((k, g)) = spike
                    && k == i
                {
                    acc[1] += (g * 9.80665) as f32;
                }
                Sample {
                    t_dev: i as f64 * DT,
                    acc,
                    gyro: [w.to_radians() as f32, 0.0, 0.0],
                    saturated: false,
                }
            })
            .collect()
    }

    /// Smooth bump: `peak` dps, `width_s` long, starting at sample `at`
    fn bump(p: &mut [f64], at: usize, width_s: f64, peak: f64) {
        let n = (width_s / DT) as usize;
        for k in 0..n {
            let u = k as f64 / n as f64;
            p[at + k] = p[at + k].max(peak * (std::f64::consts::PI * u).sin());
        }
    }

    fn detect_plain(s: &[Sample]) -> Vec<Swing> {
        detect(s, &[], [0.0; 3], 0.0, 0.0, 500.0)
    }

    #[test]
    fn threshold_crossing_and_hysteresis_end() {
        let mut p = vec![0.0; 600];
        bump(&mut p, 100, 0.2, 800.0);
        let sw = detect_plain(&samples(&p, None));
        assert_eq!(sw.len(), 1);
        let w = &sw[0];
        // Starts at the first sample above 300 dps and ends where |w| fell below 120 for good
        assert!(w.t_start > 0.2 && w.t_start < 0.24, "{}", w.t_start);
        assert!(w.t_end > 0.34 && w.t_end < 0.4, "{}", w.t_end);
        assert!((w.peak_dps - 800.0).abs() < 1.0);
        assert!((w.confidence - 1.0).abs() < 1e-6 && !w.saturated);
        // Phases are ordered
        assert!(
            w.t_start <= w.t_forward
                && w.t_forward <= w.t_peak
                && w.t_peak <= w.t_follow
                && w.t_follow <= w.t_end
        );
        // Forward swing starts where |w| first exceeds 50 % of the peak: 400 dps, well after the 300 dps start
        assert!(w.t_forward > w.t_start);
    }

    #[test]
    fn a_lobed_window_peaks_in_the_forward_swing() {
        // Fast backswing (900), a dip to 300 (still above the 120 end threshold), then the forward swing (700)
        let mut p = vec![0.0; 800];
        bump(&mut p, 100, 0.1, 900.0);
        bump(&mut p, 150, 0.1, 700.0);
        for x in &mut p[100..300] {
            *x = x.max(300.0);
        }
        let sw = detect_plain(&samples(&p, None));
        assert_eq!(sw.len(), 1);
        let w = &sw[0];
        assert!((w.peak_dps - 700.0).abs() < 1.0, "{}", w.peak_dps);
        assert!((w.t_peak - 0.35).abs() < 0.004, "{}", w.t_peak);
        assert!(
            w.t_forward > 0.3 && w.t_forward < w.t_peak,
            "{}",
            w.t_forward
        );
    }

    #[test]
    fn short_swings_are_discarded() {
        let mut p = vec![0.0; 300];
        bump(&mut p, 50, 0.07, 900.0); // 70 ms of motion (above 300 for less)
        assert!(detect_plain(&samples(&p, None)).is_empty());
        let mut q = vec![0.0; 300];
        bump(&mut q, 50, 0.2, 900.0);
        assert_eq!(detect_plain(&samples(&q, None)).len(), 1);
    }

    #[test]
    fn a_short_dip_stays_one_swing_a_long_one_splits() {
        // The sine bumps sit below 120 dps for ~10 ms at each end, so these dips are ~70 and ~110 ms below the threshold
        for (dip_s, want) in [(0.06, 1), (0.10, 2)] {
            let mut p = vec![0.0; 800];
            bump(&mut p, 100, 0.14, 800.0);
            let second = 100 + ((0.14 + dip_s) / DT) as usize;
            bump(&mut p, second, 0.14, 800.0);
            assert_eq!(detect_plain(&samples(&p, None)).len(), want, "dip {dip_s}");
        }
    }

    #[test]
    fn peak_time_is_parabola_refined() {
        // |w| = 900 - k (t - t0)^2 sampled at 2 ms with the true peak between samples
        for t0 in [0.4003, 0.4011, 0.4997] {
            let p: Vec<f64> = (0..600)
                .map(|i| {
                    let d = i as f64 * DT - t0;
                    (900.0 - 1.0e5 * d * d).max(0.0)
                })
                .collect();
            let sw = detect_plain(&samples(&p, None));
            assert_eq!(sw.len(), 1);
            assert!(
                (sw[0].t_peak - t0).abs() < 0.0005,
                "{} vs {t0}",
                sw[0].t_peak
            );
        }
    }

    #[test]
    fn impact_only_inside_window_plus_60ms() {
        let mut p = vec![0.0; 1000];
        bump(&mut p, 100, 0.2, 800.0); // window ~0.2 .. 0.36
        let sw0 = detect_plain(&samples(&p, None));
        let end = sw0[0].t_end;
        let at = |t: f64| (t / DT).round() as usize;
        // In the window
        let sw = detect_plain(&samples(&p, Some((at(0.3), 12.0))));
        let (ti, g) = sw[0].impact.unwrap();
        assert!((ti - 0.3).abs() < 0.003 && g > 10.0, "{ti} {g}");
        assert!(sw[0].timing_ms().is_some());
        // 40 ms after the end counts, 100 ms after does not, and before the start neither
        assert!(
            detect_plain(&samples(&p, Some((at(end + 0.04), 12.0))))[0]
                .impact
                .is_some()
        );
        assert!(
            detect_plain(&samples(&p, Some((at(end + 0.1), 12.0))))[0]
                .impact
                .is_none()
        );
        assert!(
            detect_plain(&samples(&p, Some((at(0.1), 12.0))))[0]
                .impact
                .is_none()
        );
        // Below the 5 g threshold: no impact
        assert!(
            detect_plain(&samples(&p, Some((at(0.3), 3.0))))[0]
                .impact
                .is_none()
        );
    }

    #[test]
    fn saturation_and_gaps_lower_confidence_and_clock_is_applied() {
        let mut p = vec![0.0; 600];
        bump(&mut p, 100, 0.2, 800.0);
        let mut s = samples(&p, None);
        s[150].saturated = true;
        let sw = detect_plain(&s);
        assert!(sw[0].saturated && sw[0].confidence == 0.5);
        let gap = |missing| Gap {
            t_start: 0.25,
            t_end: 0.26,
            missing,
        };
        let s = samples(&p, None);
        assert_eq!(
            detect(&s, &[gap(3)], [0.0; 3], 0.0, 0.0, 500.0)[0].confidence,
            1.0
        );
        assert_eq!(
            detect(&s, &[gap(4)], [0.0; 3], 0.0, 0.0, 500.0)[0].confidence,
            0.5
        );
        // t_video = t_dev + 10
        let shifted = detect(&s, &[], [0.0; 3], 0.0, 10.0, 500.0);
        assert!((shifted[0].t_start - sw[0].t_start - 10.0).abs() < 1e-9);
    }

    #[test]
    fn bias_is_removed() {
        // 200 dps of bias alone must not start a swing, but also must not hide a real one
        let p = vec![0.0; 600];
        let bias = [200f64.to_radians(), 0.0, 0.0];
        let mut s = samples(&p, None);
        for x in &mut s {
            x.gyro[0] = bias[0] as f32;
        }
        assert!(detect(&s, &[], bias, 0.0, 0.0, 500.0).is_empty());
        assert_eq!(detect(&s, &[], [0.0; 3], 0.0, 0.0, 500.0).len(), 0); // 200 < 300 either way
        for x in &mut s[100..200] {
            x.gyro[0] += 500f64.to_radians() as f32;
        }
        assert_eq!(detect(&s, &[], bias, 0.0, 0.0, 500.0).len(), 1);
    }

    #[test]
    fn follow_through_lobe_after_the_forward_swing_is_not_the_peak() {
        // backswing 900 (0.2-0.3 s), forward 800 (0.3-0.4 s), follow-through 500 (0.4-0.5 s, above half of the 900 max),
        // never dipping below 300 dps so it is one window. Impact at the forward peak.
        let mut p = vec![0.0; 800];
        bump(&mut p, 100, 0.1, 900.0);
        bump(&mut p, 150, 0.1, 800.0);
        bump(&mut p, 200, 0.1, 500.0);
        for x in &mut p[100..250] {
            *x = x.max(300.0);
        }
        let sw = detect_plain(&samples(&p, Some((175, 12.0))));
        assert_eq!(sw.len(), 1);
        assert!(
            (sw[0].t_peak - 0.35).abs() < 0.01,
            "peak {} dps at {} s",
            sw[0].peak_dps,
            sw[0].t_peak
        );
    }

    fn three_lobes(third: f64) -> Vec<f64> {
        let mut p = vec![0.0; 800];
        bump(&mut p, 100, 0.1, 900.0);
        bump(&mut p, 150, 0.1, 800.0);
        bump(&mut p, 200, 0.1, third);
        for x in &mut p[100..250] {
            *x = x.max(300.0);
        }
        p
    }

    #[test]
    fn air_swing_picks_the_latest_lobe_at_75_percent_of_the_max() {
        // 900 / 800 / 500, no impact: 500 < 675 so the forward swing (800 at 0.35 s) wins
        let sw = detect_plain(&samples(&three_lobes(500.0), None));
        assert!((sw[0].t_peak - 0.35).abs() < 0.01 && (sw[0].peak_dps - 800.0).abs() < 1.0);
        // 900 / 800 / 700, no impact: the known ambiguity, the follow-through lobe (700 at 0.45 s) is picked
        let sw = detect_plain(&samples(&three_lobes(700.0), None));
        assert!((sw[0].t_peak - 0.45).abs() < 0.01 && (sw[0].peak_dps - 700.0).abs() < 1.0);
        // The same profile with an impact at the forward peak resolves it
        let sw = detect_plain(&samples(&three_lobes(700.0), Some((175, 12.0))));
        assert!((sw[0].t_peak - 0.35).abs() < 0.01);
        assert!(sw[0].timing_ms().unwrap().abs() < 10.0);
    }

    #[test]
    fn single_lobe_peak_is_the_plain_maximum() {
        let mut p = vec![0.0; 600];
        bump(&mut p, 100, 0.2, 800.0);
        let s = samples(&p, None);
        let imax = (0..p.len()).max_by(|&a, &b| p[a].total_cmp(&p[b])).unwrap();
        let sw = detect_plain(&s);
        assert!((sw[0].t_peak - imax as f64 * DT).abs() < DT);
        assert!((sw[0].peak_dps - 800.0).abs() < 1.0);
    }

    /// Same synthetic session as app/test/wrist.test.ts and OsoKit's wristSessionAnalyzesAt200Hz: 3 s at rest, then a
    /// 0.4 s rotation about z peaking at 900 dps with a 10 g tap at its peak. Built at `rate` Hz.
    fn wrist_session(rate: f64) -> Vec<Sample> {
        (0..(4.0 * rate) as usize)
            .map(|i| {
                let t = i as f64 / rate;
                let u = t - 3.0;
                let w = if u > 0.0 && u < 0.4 {
                    900f64.to_radians() * (std::f64::consts::PI * u / 0.4).sin()
                } else {
                    0.0
                };
                let tap = if (u - 0.2).abs() < 0.003 { 10.0 } else { 0.0 };
                Sample {
                    t_dev: t,
                    acc: [(tap * 9.80665) as f32, 0.0, 9.80665],
                    gyro: [0.0, 0.0, w as f32],
                    saturated: false,
                }
            })
            .collect()
    }

    #[test]
    fn swing_and_impact_agree_across_rates() {
        let run = |rate: f64| detect(&wrist_session(rate), &[], [0.0; 3], 0.0, 0.0, rate);
        let (hi, lo) = (run(500.0), run(200.0));
        assert_eq!((hi.len(), lo.len()), (1, 1), "{hi:?} {lo:?}");
        // The wrist test's acceptance numbers: peak 900 dps within 5, impact present
        assert!((lo[0].peak_dps - 900.0).abs() < 5.0, "{}", lo[0].peak_dps);
        assert!((hi[0].peak_dps - 900.0).abs() < 5.0);
        let (ih, il) = (hi[0].impact.unwrap(), lo[0].impact.unwrap());
        assert!(
            (il.0 - 3.2).abs() < 0.005 && (ih.0 - 3.2).abs() < 0.005,
            "{ih:?} {il:?}"
        );
        assert!((ih.0 - il.0).abs() < 0.005);
        assert!(il.1 > 5.0);
        assert!((hi[0].t_peak - lo[0].t_peak).abs() < 0.005);
        assert!((hi[0].t_start - lo[0].t_start).abs() < 0.005);
        assert!((hi[0].t_end - lo[0].t_end).abs() < 0.005);
        // Head speed from the rotation at the peak, as the wrist test checks (0.3 m forearm lever there)
        assert!((lo[0].head_speed_rot() - lo[0].peak_dps.to_radians() * HEAD_CENTER).abs() < 1e-9);
    }
}
