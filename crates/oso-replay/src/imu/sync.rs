//! Device clock -> video clock: `t_video = t_dev * (1 + a) + b`.
//!
//! A clap (a sharp tap, annotated in video) at the start and another at the end of the session fix `b` and `a`: an exact
//! line through the two. Drift precision is roughly (clap timing error) / (session length), e.g. one 16.7 ms frame over
//! 10 min is about 28 ppm. With one clap `a` is unknown (0). Racquet impacts (narrow accel spikes) matched to video
//! contact times are only a check: their residuals against the clap fit are reported, not fitted.

use super::G;
use super::packet::Sample;
use oso_render::replay::Replay;

pub const CLAP_G: f64 = 6.0;
pub const IMPACT_G: f64 = 5.0;
/// Spike width limit in samples: 3 (6 ms) at 500 Hz, never fewer (Apple Watch sessions are 200 Hz).
fn max_width(rate_hz: f64) -> usize {
    ((0.006 * rate_hz).round() as usize).max(3)
}
/// Half-width of the median window that defines the "background" a spike stands out from
const HALF: usize = 4;
const MATCH_WINDOW_S: f64 = 0.1;
/// Right wrist
const WRIST: usize = 10;
/// Wrist speed above this (m/s) counts as a swing; backswings and walking stay below
const MIN_SWING_SPEED: f32 = 7.0;
const MIN_PEAK_SEPARATION_S: f64 = 0.5;
/// Quartz drifts tens of ppm; a larger slope between the claps means a mismatched clap, not the clock
pub const MAX_DRIFT: f64 = 200e-6;

/// Per-sample deviation of |accel| components from the local median, in g; 0 next to a lost-packet gap.
pub fn spike_strength(samples: &[Sample], rate_hz: f64) -> Vec<f64> {
    // The 9-sample window (nominal span 16 ms at 500 Hz) still passes with one lost packet inside, not with a bigger hole
    let max_span = 9.5 / rate_hz;
    let n = samples.len();
    let mut out = vec![0.0; n];
    if n < 2 * HALF + 1 {
        return out;
    }
    for i in HALF..n - HALF {
        let win = &samples[i - HALF..=i + HALF];
        if win[2 * HALF].t_dev - win[0].t_dev > max_span {
            continue;
        }
        let mut d2 = 0.0;
        for a in 0..3 {
            let mut v: [f32; 2 * HALF + 1] = std::array::from_fn(|k| win[k].acc[a]);
            v.sort_by(f32::total_cmp);
            let dev = (samples[i].acc[a] - v[HALF]) as f64 / G;
            d2 += dev * dev;
        }
        out[i] = d2.sqrt();
    }
    out
}

/// Narrow spikes (<= 6 ms at 500 Hz, see `max_width`) above `thr_g` at or after sample `from`: centroid device time of
/// each. `rate_hz`: the capture's sample rate.
pub fn find_spikes(samples: &[Sample], from: usize, thr_g: f64, rate_hz: f64) -> Vec<f64> {
    spikes(
        samples,
        &spike_strength(samples, rate_hz),
        from,
        thr_g,
        rate_hz,
    )
}

fn spikes(samples: &[Sample], strength: &[f64], from: usize, thr_g: f64, rate_hz: f64) -> Vec<f64> {
    spikes_with_peak(samples, strength, from, thr_g, rate_hz)
        .into_iter()
        .map(|s| s.0)
        .collect()
}

/// Like [`find_spikes`] on a precomputed [`spike_strength`]: (centroid device time, peak strength in g) per spike.
pub fn spikes_with_peak(
    samples: &[Sample],
    strength: &[f64],
    from: usize,
    thr_g: f64,
    rate_hz: f64,
) -> Vec<(f64, f64)> {
    let max_width = max_width(rate_hz);
    let mut out = Vec::new();
    let mut i = from;
    while i < samples.len() {
        if strength[i] <= thr_g {
            i += 1;
            continue;
        }
        let s = i;
        while i < samples.len() && strength[i] > thr_g {
            i += 1;
        }
        if i - s <= max_width {
            // One extra sample each side: the amplitude of the flanks says where between samples the pulse peaked
            let (lo, hi) = (s.saturating_sub(1), (i + 1).min(samples.len()));
            let (num, den) = (lo..hi).fold((0.0, 0.0), |(n, d), k| {
                (n + samples[k].t_dev * strength[k], d + strength[k])
            });
            let peak = strength[s..i].iter().cloned().fold(0.0, f64::max);
            out.push((num / den, peak));
        }
    }
    out
}

/// First spike above CLAP_G at or after `from` (the end of the stationary window).
pub fn find_clap(samples: &[Sample], from: usize, rate_hz: f64) -> Option<f64> {
    find_spikes(samples, from, CLAP_G, rate_hz)
        .into_iter()
        .next()
}

/// Where the video-side contact times came from
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContactSource {
    /// The replay's `events` track: racquet-shuttle contacts from the fitted shuttle flights
    Shuttle,
    /// Right-wrist speed peaks
    WristProxy,
}

impl ContactSource {
    pub fn label(self) -> &'static str {
        match self {
            ContactSource::Shuttle => "shuttle contacts (events track)",
            ContactSource::WristProxy => "wrist-speed proxy",
        }
    }
}

/// Video-side contact times for `player` and which source they came from: the shuttle contact events when the replay
/// has any for that player, else the wrist-speed proxy.
pub fn video_contacts_with_source(replay: &Replay, player: usize) -> (Vec<f64>, ContactSource) {
    let c: Vec<f64> = replay
        .events()
        .iter()
        .filter(|e| e[1] == oso_render::replay::EVENT_CONTACT && e[2] == player as f32)
        .map(|e| e[0] as f64)
        .collect();
    if c.is_empty() {
        (wrist_contacts(replay, player), ContactSource::WristProxy)
    } else {
        (c, ContactSource::Shuttle)
    }
}

pub fn video_contacts(replay: &Replay, player: usize) -> Vec<f64> {
    video_contacts_with_source(replay, player).0
}

/// Fallback contact proxy: right-wrist speed peak per swing, parabola-refined to sub-frame, seconds.
/// ponytail: the wrist-speed-peak proxy is biased vs true racket-shuttle contact by a few ms (and quantized by the 60 fps
/// frames); used only when the replay has no shuttle contacts.
fn wrist_contacts(replay: &Replay, player: usize) -> Vec<f64> {
    let n = replay.frame_count;
    let fps = replay.fps as f64;
    let speed: Vec<f32> = (0..n)
        .map(|f| {
            if f == 0 || f + 1 >= n {
                return 0.0;
            }
            let (a, b) = (
                replay.joints(f - 1, player)[WRIST],
                replay.joints(f + 1, player)[WRIST],
            );
            if a[3] <= 0.0 || b[3] <= 0.0 {
                return 0.0;
            }
            let d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
            (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt() * (fps as f32) / 2.0
        })
        .collect();
    let radius = 3;
    let mut peaks: Vec<(f32, f64)> = Vec::new();
    for f in radius..n.saturating_sub(radius) {
        let s = speed[f];
        if s < MIN_SWING_SPEED || speed[f - radius..=f + radius].iter().any(|&o| o > s) {
            continue;
        }
        let (l, r) = (speed[f - 1] as f64, speed[f + 1] as f64);
        let den = l - 2.0 * s as f64 + r;
        let off = if den < -1e-9 {
            0.5 * (l - r) / den
        } else {
            0.0
        };
        let t = (f as f64 + off.clamp(-0.5, 0.5)) / fps;
        // Plateaus produce neighbours: keep the stronger of two peaks closer than the separation
        match peaks.last_mut() {
            Some(last) if t - last.1 < MIN_PEAK_SEPARATION_S => {
                if s > last.0 {
                    *last = (s, t);
                }
            }
            _ => peaks.push((s, t)),
        }
    }
    peaks.into_iter().map(|p| p.1).collect()
}

/// An IMU impact matched to a video contact: device time, video time of the contact proxy, and how far the clap fit
/// places it from the proxy (ms, fit minus video).
#[derive(Debug, Clone, Copy)]
pub struct Pair {
    pub t_dev: f64,
    pub t_video: f64,
    pub residual_ms: f64,
}

#[derive(Debug, Clone)]
pub struct SyncResult {
    /// t_video = t_dev * (1 + a) + b
    pub a: f64,
    pub b: f64,
    /// Offset from the first clap alone (a = 0)
    pub b_clap: f64,
    /// False with a single clap (or an implausible slope): a = 0 and the drift is unknown
    pub drift_known: bool,
    /// (t_dev, t_video) of each clap used
    pub claps: Vec<(f64, f64)>,
    /// A check only: impacts are not used in the fit
    pub impacts: Vec<Pair>,
    pub rms_ms: f64,
    pub max_ms: f64,
    pub contact_source: ContactSource,
}

/// Exact line through the first and last clap `(t_dev, t_video)`. One clap: a = 0 (drift unknown). A slope beyond
/// MAX_DRIFT is a mismatched clap, not a clock: same fallback. Returns (a, b, drift_known).
pub fn fit_claps(claps: &[(f64, f64)]) -> (f64, f64, bool) {
    let first = claps[0];
    let b0 = first.1 - first.0;
    if claps.len() >= 2 {
        let last = claps[claps.len() - 1];
        let slope = (last.1 - first.1) / (last.0 - first.0);
        if slope.is_finite() && (slope - 1.0).abs() <= MAX_DRIFT {
            let a = slope - 1.0;
            return (a, first.1 - first.0 * slope, true);
        }
    }
    (0.0, b0, false)
}

/// The second clap: a spike above CLAP_G within 0.5 s of where the video annotation predicts it, with the racquet still
/// for the 0.3 s before it (mean |gyro| < 30 dps; the bias is up to ~5 dps). Closest to the prediction wins.
fn find_end_clap(
    samples: &[Sample],
    strength: &[f64],
    from: usize,
    predicted: f64,
    rate_hz: f64,
) -> Option<f64> {
    spikes(samples, strength, from, CLAP_G, rate_hz)
        .into_iter()
        .filter(|&t| (t - predicted).abs() < 0.5)
        .filter(|&t| {
            let lo = samples.partition_point(|s| s.t_dev < t - 0.3);
            let hi = samples.partition_point(|s| s.t_dev < t - 0.02);
            let w = &samples[lo..hi];
            !w.is_empty()
                && w.iter()
                    .map(|s| super::norm(s.gyro.map(|g| g as f64)))
                    .sum::<f64>()
                    / (w.len() as f64)
                    < 30f64.to_radians()
        })
        .min_by(|a, b| (a - predicted).abs().total_cmp(&(b - predicted).abs()))
}

/// Full sync. `claps_video`: video times of the start clap and (optionally) the end clap. `window_end`: end of the
/// stationary window (sample index). `rate_hz`: the capture's sample rate. None if the start clap is not found.
pub fn sync(
    samples: &[Sample],
    window_end: usize,
    claps_video: &[f64],
    replay: &Replay,
    player: usize,
    rate_hz: f64,
) -> Option<SyncResult> {
    let strength = spike_strength(samples, rate_hz);
    let t0 = *spikes(samples, &strength, window_end, CLAP_G, rate_hz).first()?;
    let mut claps = vec![(t0, claps_video[0])];
    if claps_video.len() >= 2 {
        let predicted = t0 + (claps_video[claps_video.len() - 1] - claps_video[0]);
        if let Some(t1) = find_end_clap(samples, &strength, window_end, predicted, rate_hz) {
            claps.push((t1, claps_video[claps_video.len() - 1]));
        }
    }
    let (a, b, drift_known) = fit_claps(&claps);
    let to_video = |t: f64| t * (1.0 + a) + b;
    let (contacts, contact_source) = video_contacts_with_source(replay, player);
    let mut used = vec![false; contacts.len()];
    let mut impacts = Vec::new();
    for t_dev in spikes(samples, &strength, window_end, IMPACT_G, rate_hz) {
        // The claps are spikes too
        if claps.iter().any(|c| (t_dev - c.0).abs() < 0.05) {
            continue;
        }
        let pred = to_video(t_dev);
        let best = contacts
            .iter()
            .enumerate()
            .filter(|(i, _)| !used[*i])
            .map(|(i, c)| (i, (c - pred).abs()))
            .filter(|p| p.1 <= MATCH_WINDOW_S)
            .min_by(|a, b| a.1.total_cmp(&b.1));
        if let Some((i, _)) = best {
            used[i] = true;
            impacts.push(Pair {
                t_dev,
                t_video: contacts[i],
                residual_ms: (pred - contacts[i]) * 1e3,
            });
        }
    }
    let n = impacts.len().max(1) as f64;
    let rms_ms = (impacts.iter().map(|p| p.residual_ms.powi(2)).sum::<f64>() / n).sqrt();
    let max_ms = impacts
        .iter()
        .map(|p| p.residual_ms.abs())
        .fold(0.0, f64::max);
    Some(SyncResult {
        a,
        b,
        b_clap: claps[0].1 - claps[0].0,
        drift_known,
        claps,
        impacts,
        rms_ms,
        max_ms,
        contact_source,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use oso_render::replay::JOINTS;

    fn flat(n: usize) -> Vec<Sample> {
        (0..n)
            .map(|i| Sample {
                t_dev: i as f64 * 0.002,
                acc: [0.0, 0.0, G as f32],
                gyro: [0.0; 3],
                saturated: false,
            })
            .collect()
    }

    /// The on-device detector (oso-proto) agrees with the host clap finder on the synthetic capture: both claps
    /// within 2 ms, nothing during the swings. Skipped when out/synth is not generated.
    #[test]
    fn firmware_tap_detector_finds_the_claps() {
        let Ok(bytes) = std::fs::read("out/synth/imu.bin") else {
            eprintln!("out/synth/imu.bin missing: skipped");
            return;
        };
        let c = crate::imu::packet::parse(&bytes).unwrap();
        let mut det = oso_proto::TapDetector::new();
        let t0 = u32::from_le_bytes(bytes[18..22].try_into().unwrap());
        let taps: Vec<f64> = bytes[crate::imu::packet::HEADER_LEN..]
            .as_chunks::<{ oso_proto::PACKET_LEN }>()
            .0
            .iter()
            .filter_map(|p| det.push(oso_proto::Packet::decode(p)))
            .map(|t| t.wrapping_sub(t0) as f64 * 1e-6 + c.samples[0].t_dev)
            .collect();
        let host = find_spikes(&c.samples, 0, CLAP_G, c.rate_hz as f64);
        assert_eq!(taps.len(), 2, "{taps:?} vs host {host:?}");
        for t in &taps {
            assert!(
                host.iter().any(|h| (h - t).abs() < 0.002),
                "{t} vs {host:?}"
            );
        }
        assert!(taps[1] - taps[0] > 5.0, "{taps:?}");
    }

    #[test]
    fn two_claps_give_exact_a_b() {
        let (a, b) = (-40e-6, -3.217);
        let to_v = |t: f64| t * (1.0 + a) + b;
        let (a2, b2, known) = fit_claps(&[(3.467, to_v(3.467)), (12.8, to_v(12.8))]);
        assert!(known && (a2 - a).abs() < 1e-12 && (b2 - b).abs() < 1e-12);
        // One clap: offset only, drift unknown
        let (a1, b1, known) = fit_claps(&[(3.4, 0.25)]);
        assert_eq!((a1, b1, known), (0.0, 0.25 - 3.4, false));
        // A second clap that implies 1000 ppm is a mismatch: not taken as drift
        let (a3, b3, known) = fit_claps(&[(3.4, 0.25), (13.4, 10.26)]);
        assert_eq!((a3, b3, known), (0.0, 0.25 - 3.4, false));
    }

    #[test]
    fn end_clap_needs_stillness_and_the_predicted_time() {
        let mut s = flat(4000);
        let spike = |s: &mut Vec<Sample>, k: usize| {
            for x in &mut s[k..k + 2] {
                x.acc[2] += (12.0 * G) as f32;
            }
        };
        spike(&mut s, 500);
        spike(&mut s, 3000);
        let strength = spike_strength(&s, 500.0);
        // Predicted 6.0 s = sample 3000
        assert!((find_end_clap(&s, &strength, 0, 6.001, 500.0).unwrap() - 6.001).abs() < 1e-9);
        assert!(
            find_end_clap(&s, &strength, 0, 7.0, 500.0).is_none(),
            "outside 0.5 s of the prediction"
        );
        // Racquet swinging before it: an impact, not a clap
        for x in &mut s[2700..2990] {
            x.gyro[0] = 20.0;
        }
        let strength = spike_strength(&s, 500.0);
        assert!(find_end_clap(&s, &strength, 0, 6.001, 500.0).is_none());
    }

    #[test]
    fn spikes_found_with_width_limit_and_gap_guard() {
        let mut s = flat(2000);
        // 12 g tap, 2 samples
        for k in [500, 501] {
            s[k].acc[2] += (12.0 * G) as f32;
        }
        // 5.5 g tap, 1 sample
        s[1000].acc[2] -= (5.5 * G) as f32;
        // 40 ms plateau of 10 g (a swing, not an impact), and a smooth ramp
        for x in &mut s[1300..1320] {
            x.acc[0] += (10.0 * G) as f32;
        }
        for (k, x) in s.iter_mut().enumerate().skip(1500).take(100) {
            x.acc[1] += (k - 1500) as f32 * 0.5;
        }
        let found = find_spikes(&s, 0, IMPACT_G, 500.0);
        assert_eq!(found.len(), 2, "{found:?}");
        assert!(
            (found[0] - 0.0010 - 0.002 * 500.0).abs() < 1e-9,
            "{found:?}"
        );
        assert!((found[1] - 2.0).abs() < 1e-9);
        assert_eq!(find_clap(&s, 0, 500.0), Some(found[0]));
        assert_eq!(
            find_clap(&s, 600, 500.0),
            None,
            "the 5.5 g tap is below the clap threshold"
        );
        // A hole in the timeline next to a step is not a spike
        let mut g = flat(200);
        for x in g.iter_mut().skip(100) {
            x.acc[0] += 20.0;
            x.t_dev += 0.1;
        }
        assert!(find_spikes(&g, 0, IMPACT_G, 500.0).is_empty());
    }

    fn flat_at(n: usize, rate: f64) -> Vec<Sample> {
        (0..n)
            .map(|i| Sample {
                t_dev: i as f64 / rate,
                acc: [0.0, 0.0, G as f32],
                gyro: [0.0; 3],
                saturated: false,
            })
            .collect()
    }

    /// A 12 g tap at t = 1 s (one sample at 200 Hz, two at 500 Hz) is found at the same time, within a sample period,
    /// at both rates; a 20 ms plateau is wider than the limit at either rate.
    #[test]
    fn spike_timing_agrees_across_rates() {
        let found = |rate: f64, width_s: f64| {
            let mut s = flat_at((3.0 * rate) as usize, rate);
            let k0 = rate as usize;
            for x in &mut s[k0..k0 + (width_s * rate).round() as usize] {
                x.acc[2] += (12.0 * G) as f32;
            }
            (find_spikes(&s, 0, CLAP_G, rate), find_clap(&s, 0, rate))
        };
        let (f500, c500) = found(500.0, 0.004);
        let (f200, c200) = found(200.0, 0.005);
        assert_eq!((f500.len(), f200.len()), (1, 1), "{f500:?} {f200:?}");
        assert_eq!((c500, c200), (Some(f500[0]), Some(f200[0])));
        assert!((f500[0] - f200[0]).abs() < 0.005, "{f500:?} {f200:?}");
        assert!((f200[0] - 1.0).abs() < 0.005);
        for rate in [500.0, 200.0] {
            assert!(
                found(rate, 0.02).0.is_empty(),
                "{rate} Hz plateau is a swing, not a spike"
            );
        }
    }

    /// The width limit is 6 ms at any rate: above 500 Hz it grows past 3 samples (5 at 800 Hz), and below it is floored at 3.
    #[test]
    fn max_width_scales_above_500_hz() {
        assert_eq!(
            (
                max_width(200.0),
                max_width(500.0),
                max_width(800.0),
                max_width(833.0)
            ),
            (3, 3, 5, 5)
        );
        let plateau = |rate: f64, n: usize| {
            let mut s = flat_at((2.0 * rate) as usize, rate);
            let k0 = rate as usize;
            for x in &mut s[k0..k0 + n] {
                x.acc[2] += (12.0 * G) as f32;
            }
            find_spikes(&s, 0, CLAP_G, rate).len()
        };
        assert_eq!(plateau(800.0, 4), 1, "5 ms at 800 Hz is narrow");
        assert_eq!(plateau(800.0, 8), 0, "10 ms at 800 Hz is a swing");
        assert_eq!(plateau(500.0, 4), 0, "4 samples at 500 Hz is 8 ms");
    }

    /// The gap guard scales too: with a 9-sample window, one lost packet inside still passes at 200 Hz (45 ms span
    /// against a 47.5 ms limit), two do not; same at 500 Hz (18 ms vs 19 ms, 20 ms).
    #[test]
    fn gap_guard_scales_with_rate() {
        for rate in [200.0, 500.0] {
            let dt = 1.0 / rate;
            let with_lost = |lost: usize| {
                let mut s = flat_at(200, rate);
                s[100].acc[0] += (20.0 * G) as f32;
                for x in s.iter_mut().skip(101) {
                    x.t_dev += lost as f64 * dt;
                }
                spike_strength(&s, rate)[100]
            };
            assert!(with_lost(0) > 19.0 && with_lost(1) > 19.0, "{rate} Hz");
            assert_eq!(with_lost(2), 0.0, "{rate} Hz");
        }
    }

    #[test]
    fn video_contacts_find_wrist_speed_peaks() {
        // Wrist moving along +x with a 15 m/s Gaussian speed bump centered at t = 1.2345 s (60 fps, 3 s)
        let (fps, frames, c0) = (60.0f64, 180usize, 1.2345);
        let speed = |u: f64| 15.0 * (-((u - c0) / 0.05).powi(2) / 2.0).exp();
        let mut x = vec![0.0f64; frames];
        let (mut pos, steps) = (0.0, 100);
        for (f, xf) in x.iter_mut().enumerate().skip(1) {
            for k in 0..steps {
                pos += speed((f as f64 - 1.0 + (k as f64 + 0.5) / steps as f64) / fps)
                    / fps
                    / steps as f64;
            }
            *xf = pos;
        }
        let mut joints = vec![[0.0f32, 1.0, 0.0, 1.0]; frames * JOINTS];
        for f in 0..frames {
            joints[f * JOINTS + WRIST][0] = x[f] as f32;
        }
        let r = Replay::from_parts(fps as f32, frames, 1, joints, None);
        let c = video_contacts(&r, 0);
        assert_eq!(c.len(), 1, "{c:?}");
        assert!((c[0] - c0).abs() < 0.003, "{c:?}");
        assert_eq!(
            video_contacts_with_source(&r, 0).1,
            ContactSource::WristProxy
        );

        // With shuttle contacts for the player those win (other players' and landing events are ignored)
        let events = vec![
            [0.9, 1.0, 0.0, 0.0, 1.0, 0.0],
            [1.5, 1.0, 1.0, 0.0, 1.0, 0.0],
            [2.0, 2.0, -1.0, 0.0, 0.0, 0.0],
        ];
        let r = r.with_shuttle(vec![[0.0; 8]; frames], events);
        let (c, src) = video_contacts_with_source(&r, 0);
        assert_eq!((c.len(), src), (1, ContactSource::Shuttle));
        assert!((c[0] - 0.9).abs() < 1e-6);
    }
}
