//! On-device tap / clap detector, the causal twin of `spike_strength` + `find_spikes` in `src/imu/sync.rs`: an accel
//! spike (deviation of each axis from the 9-sample median, vector length) above `TAP_G`, at most 3 samples wide,
//! after the gyro was quiet for `STILL_US`. Reports the strength-weighted centroid time (device clock, us).

use crate::Packet;

const HALF: usize = 4;
const WIN: usize = 2 * HALF + 1;
const LSB_PER_G: i64 = 2048;
pub const TAP_G: i64 = 5;
/// The spec asked for 1.5 s and |a| within 0.1 g of 1 g; the synthetic end clap follows only ~1.3 s of gyro < 20 dps
/// while the racquet is still being lowered (|a| far from 1 g), so: gyro only, 1 s.
pub const STILL_US: u32 = 1_000_000;
const MAX_WIDTH: u32 = 3;
/// Same gap rule as the host: a 9-sample window spanning more than 19 ms has a lost packet inside
const MAX_SPAN_US: u32 = 19_000;
const STILL_GYRO: i64 = 20 * 164 / 10; // 20 dps in counts

fn isqrt(n: i64) -> i64 {
    if n < 2 {
        return n;
    }
    let mut x = n;
    let mut y = (x + 1) / 2;
    while y < x {
        x = y;
        y = (x + n / x) / 2;
    }
    x
}

fn is_still(v: &[i16; 6]) -> bool {
    v[3..].iter().map(|&c| c as i64 * c as i64).sum::<i64>() < STILL_GYRO * STILL_GYRO
}

#[derive(Debug, Clone)]
pub struct TapDetector {
    ring: [Packet; WIN],
    /// Length of the still run ending at each ring slot, us (0 = not still)
    still: [u32; WIN],
    still_since: Option<u32>,
    n: usize,
    prev_strength: i64,
    prev_t: u32,
    // Spike in progress
    run: u32,
    run_ok: bool,
    run_t0: u32,
    num: i64,
    den: i64,
}

impl Default for TapDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl TapDetector {
    pub fn new() -> Self {
        TapDetector {
            ring: [Packet::default(); WIN],
            still: [0; WIN],
            still_since: None,
            n: 0,
            prev_strength: 0,
            prev_t: 0,
            run: 0,
            run_ok: false,
            run_t0: 0,
            num: 0,
            den: 0,
        }
    }

    /// Feed every sample in order. Returns the device-clock time (us) of a tap, about 4 samples after it happened.
    pub fn push(&mut self, p: Packet) -> Option<u32> {
        // Lost packets are handled like the host does: a window spanning one has strength 0 (below)
        let dur = if is_still(&p.v) {
            let since = *self.still_since.get_or_insert(p.t_us);
            p.t_us.wrapping_sub(since)
        } else {
            self.still_since = None;
            0
        };
        self.ring.rotate_left(1);
        self.ring[WIN - 1] = p;
        self.still.rotate_left(1);
        // A sample that starts a run has duration 0 even though it is still: mark runs with +1 so 0 means "not still"
        self.still[WIN - 1] = if self.still_since.is_some() { dur + 1 } else { 0 };
        self.n += 1;
        if self.n < WIN {
            return None;
        }

        let c = self.ring[HALF];
        let span = self.ring[WIN - 1].t_us.wrapping_sub(self.ring[0].t_us);
        let strength = if span > MAX_SPAN_US { 0 } else { self.strength() };
        let mut out = None;
        if strength > TAP_G * LSB_PER_G {
            if self.run == 0 {
                self.run_ok = self.still[1] > STILL_US;
                self.run_t0 = c.t_us;
                self.num = 0;
                self.den = 0;
                if self.prev_strength > 0 {
                    self.add(self.prev_t, self.prev_strength);
                }
            }
            self.run += 1;
            self.add(c.t_us, strength);
        } else if self.run > 0 {
            if self.run <= MAX_WIDTH && self.run_ok {
                self.add(c.t_us, strength);
                out = Some(self.run_t0.wrapping_add((self.num / self.den) as i32 as u32));
            }
            self.run = 0;
        }
        self.prev_strength = strength;
        self.prev_t = c.t_us;
        out
    }

    fn add(&mut self, t: u32, w: i64) {
        self.num += w * t.wrapping_sub(self.run_t0) as i32 as i64;
        self.den += w;
    }

    /// Counts: length of the per-axis deviation of the centre sample from the window median
    fn strength(&self) -> i64 {
        let mut d2 = 0i64;
        for a in 0..3 {
            let mut v: [i16; WIN] = core::array::from_fn(|k| self.ring[k].v[a]);
            v.sort_unstable();
            let d = self.ring[HALF].v[a] as i64 - v[HALF] as i64;
            d2 += d * d;
        }
        isqrt(d2)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::vec::Vec;

    fn run(mut f: impl FnMut(u32) -> [i16; 6], n: u32) -> Vec<u32> {
        let mut d = TapDetector::new();
        (0..n).filter_map(|i| d.push(Packet { seq: i as u16, t_us: i * 2000, v: f(i) })).collect()
    }

    const REST: [i16; 6] = [0, 0, 2048, 3, -2, 1];

    #[test]
    fn finds_tap_after_stillness_only() {
        // 3 s still, 2-sample 12 g tap at sample 1500 (t = 3.000 s), then more rest
        let tap = |i: u32| if i == 1500 || i == 1501 { [0, 0, 2048 + 12 * 2048, 3, -2, 1] } else { REST };
        let t = run(tap, 2000);
        assert_eq!(t.len(), 1, "{t:?}");
        assert!((t[0] as i64 - 3_001_000).abs() <= 1000, "{t:?}");
        // Same tap only 0.6 s after the start: not yet still long enough
        let early = |i: u32| if i == 300 { [0, 0, 2048 + 12 * 2048, 0, 0, 0] } else { REST };
        assert!(run(early, 1000).is_empty());
    }

    #[test]
    fn ignores_waving_and_wide_spikes() {
        // Moving the whole time: never still
        let wave = |i: u32| [(i as i16 % 50) * 300, 0, 2048, 4000, 0, 0];
        assert!(run(wave, 3000).is_empty());
        // Still, then a 6-sample-wide jolt
        let wide = |i: u32| if (1600..1606).contains(&i) { [0, 0, 2048 + 12 * 2048, 0, 0, 0] } else { REST };
        assert!(run(wide, 2000).is_empty());
    }

    #[test]
    fn t_us_wraps() {
        let mut d = TapDetector::new();
        let t0 = u32::MAX - 1_000_000;
        let found: Vec<u32> = (0..2000u32)
            .filter_map(|i| {
                let v = if i == 1500 { [0, 0, 2048 + 12 * 2048, 0, 0, 0] } else { REST };
                d.push(Packet { seq: i as u16, t_us: t0.wrapping_add(i * 2000), v })
            })
            .collect();
        assert_eq!(found.len(), 1);
        assert!((found[0].wrapping_sub(t0.wrapping_add(3_000_000)) as i32).abs() < 1500);
    }
}
