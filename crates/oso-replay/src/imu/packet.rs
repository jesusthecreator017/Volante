//! imu.bin decoding (format: docs/data-format.md): 16-byte header + 18-byte little-endian packets
//! `u16 seq, u32 t_us, i16 ax ay az gx gy gz` in arrival order.

use super::G;

pub const HEADER_LEN: usize = 16;
pub const PACKET_LEN: usize = oso_proto::PACKET_LEN;
/// MPU-9250 at +-16 g and +-2000 dps
pub const ACC_LSB_PER_G: f64 = 2048.0;
pub const GYRO_LSB_PER_DPS: f64 = 16.4;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sample {
    /// Device clock, seconds (unwrapped from the u32 microseconds)
    pub t_dev: f64,
    /// Specific force in the sensor frame, m/s^2
    pub acc: [f32; 3],
    /// Angular rate in the sensor frame, rad/s (bias not removed)
    pub gyro: [f32; 3],
    /// A gyro axis sits at the int16 limit: the true rate was at or beyond +-2000 dps
    pub saturated: bool,
}

/// Packets missing between two received neighbours.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Gap {
    pub t_start: f64,
    pub t_end: f64,
    pub missing: u64,
}

/// Packet accounting. `reordered`: a first-seen seq lower than one already seen (it arrived late);
/// `duplicate`: a seq seen before; `lost`: seqs missing between the first and last received.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Stats {
    pub arrivals: usize,
    pub unique: usize,
    pub lost: u64,
    pub duplicate: usize,
    pub reordered: usize,
}

#[derive(Debug)]
pub struct Capture {
    pub rate_hz: u16,
    /// Sorted by seq, duplicates dropped
    pub samples: Vec<Sample>,
    pub gaps: Vec<Gap>,
    pub stats: Stats,
}

/// Scales one raw packet (device time in unwrapped microseconds) to SI units
pub fn to_sample(t_us: i64, v: [i16; 6]) -> Sample {
    let acc_k = G / ACC_LSB_PER_G;
    let gyro_k = (std::f64::consts::PI / 180.0) / GYRO_LSB_PER_DPS;
    Sample {
        t_dev: t_us as f64 * 1e-6,
        acc: std::array::from_fn(|i| (v[i] as f64 * acc_k) as f32),
        gyro: std::array::from_fn(|i| (v[3 + i] as f64 * gyro_k) as f32),
        saturated: v[3..].iter().any(|&c| c == i16::MAX || c == i16::MIN),
    }
}

pub fn parse(bytes: &[u8]) -> anyhow::Result<Capture> {
    anyhow::ensure!(
        bytes.len() >= HEADER_LEN,
        "imu file shorter than its header"
    );
    anyhow::ensure!(&bytes[..4] == b"OSOI", "bad imu magic");
    let version = u16::from_le_bytes([bytes[4], bytes[5]]);
    anyhow::ensure!(version == 1, "unsupported imu version {version}");
    let rate_hz = u16::from_le_bytes([bytes[6], bytes[7]]);
    let body = &bytes[HEADER_LEN..];
    anyhow::ensure!(
        body.len().is_multiple_of(PACKET_LEN),
        "imu body of {} bytes is not a whole number of {PACKET_LEN}-byte packets",
        body.len()
    );

    struct Raw {
        seq: i64,
        t_us: i64,
        v: [i16; 6],
    }
    let mut raws = Vec::with_capacity(body.len() / PACKET_LEN);
    let mut prev: Option<(u16, i64, u32, i64)> = None;
    for p in body.as_chunks::<PACKET_LEN>().0 {
        let oso_proto::Packet { seq: seq16, t_us: t32, v } = oso_proto::Packet::decode(p);
        // Unwrap against the previous arrival: the signed 16/32-bit difference is the true step even when
        // a reordered packet straddles the wrap.
        let (seq, t_us) = match prev {
            None => (seq16 as i64, t32 as i64),
            Some((ps, pu, pt, ptu)) => (
                pu + seq16.wrapping_sub(ps) as i16 as i64,
                ptu + t32.wrapping_sub(pt) as i32 as i64,
            ),
        };
        prev = Some((seq16, seq, t32, t_us));
        raws.push(Raw { seq, t_us, v });
    }

    let mut stats = Stats {
        arrivals: raws.len(),
        ..Stats::default()
    };
    let mut top = i64::MIN;
    let mut seen = std::collections::HashSet::with_capacity(raws.len());
    for r in &raws {
        if !seen.insert(r.seq) {
            stats.duplicate += 1;
        } else if r.seq < top {
            stats.reordered += 1;
        }
        top = top.max(r.seq);
    }
    // Stable sort + dedup keeps the first arrival of a repeated seq
    raws.sort_by_key(|r| r.seq);
    raws.dedup_by_key(|r| r.seq);
    stats.unique = raws.len();

    let mut gaps = Vec::new();
    for w in raws.windows(2) {
        let missing = (w[1].seq - w[0].seq - 1) as u64;
        if missing > 0 {
            stats.lost += missing;
            gaps.push(Gap {
                t_start: w[0].t_us as f64 * 1e-6,
                t_end: w[1].t_us as f64 * 1e-6,
                missing,
            });
        }
    }

    let samples = raws.iter().map(|r| to_sample(r.t_us, r.v)).collect();
    Ok(Capture {
        rate_hz,
        samples,
        gaps,
        stats,
    })
}

/// Encodes a capture in file layout (tests, benches, and the Python generator's twin).
pub fn encode(rate_hz: u16, packets: &[(u16, u32, [i16; 6])]) -> Vec<u8> {
    let mut out = b"OSOI".to_vec();
    out.extend(1u16.to_le_bytes());
    out.extend(rate_hz.to_le_bytes());
    out.extend([0u8; 8]);
    for &(seq, t_us, v) in packets {
        out.extend(oso_proto::Packet { seq, t_us, v }.encode());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pk(seq: u16, t_us: u32) -> (u16, u32, [i16; 6]) {
        (seq, t_us, [seq as i16, 0, 2048, 0, 0, 0])
    }

    #[test]
    fn decodes_scales_and_saturation() {
        let bytes = encode(500, &[(7, 2000, [2048, -2048, 0, 164, i16::MAX, 0])]);
        let c = parse(&bytes).unwrap();
        let s = c.samples[0];
        assert_eq!(c.rate_hz, 500);
        assert!((s.acc[0] as f64 - G).abs() < 1e-5 && (s.acc[1] as f64 + G).abs() < 1e-5);
        assert!((s.gyro[0] as f64 - 10f64.to_radians()).abs() < 1e-6);
        assert!(s.saturated && (s.t_dev - 0.002).abs() < 1e-12);
        assert!(!parse(&encode(500, &[pk(0, 0)])).unwrap().samples[0].saturated);
    }

    #[test]
    fn unwraps_seq_and_time_sorts_dedupes_and_counts() {
        // Arrival: 65534 65535 0 (wrapped) 2 1 (late) 2 (dup) 5 (3, 4 lost); t_us wraps at the same place
        let t0 = u32::MAX - 3_000;
        let arrivals = [
            (65534u16, 0u32),
            (65535, 1),
            (0, 2),
            (2, 4),
            (1, 3),
            (2, 4),
            (5, 7),
        ];
        let packets: Vec<_> = arrivals
            .iter()
            .map(|&(s, k)| pk(s, t0.wrapping_add(k * 2000)))
            .collect();
        let c = parse(&encode(500, &packets)).unwrap();
        assert_eq!(
            c.stats,
            Stats {
                arrivals: 7,
                unique: 6,
                lost: 2,
                duplicate: 1,
                reordered: 1
            }
        );
        assert_eq!(c.samples.len(), 6);
        let t: Vec<f64> = c.samples.iter().map(|s| s.t_dev).collect();
        assert!(t.windows(2).all(|w| w[1] > w[0]), "{t:?}");
        // t_us wrapped: unwrapped time keeps counting past 2^32 us
        assert!(t[5] > 4294.9 && (t[3] - t[2] - 0.002).abs() < 1e-9, "{t:?}");
        assert_eq!(
            c.gaps,
            vec![Gap {
                t_start: t[4],
                t_end: t[5],
                missing: 2
            }]
        );
        // The sample values follow their seq (acc x carries the raw seq as a marker)
        assert_eq!(c.samples[2].acc[0], (0.0 * G / ACC_LSB_PER_G) as f32);
    }

    #[test]
    fn rejects_malformed() {
        assert!(parse(b"").is_err());
        assert!(parse(b"OSOI").is_err());
        let ok = encode(500, &[pk(0, 0)]);
        assert!(parse(&ok).is_ok());
        assert!(parse(&ok[..ok.len() - 1]).is_err());
        let mut bad = ok.clone();
        bad[0] = b'X';
        assert!(parse(&bad).is_err());
        let mut bad = ok.clone();
        bad[4] = 2;
        assert!(parse(&bad).is_err());
        let mut extra = ok;
        extra.push(0);
        assert!(parse(&extra).is_err());
    }

    #[test]
    fn empty_capture_is_fine() {
        let c = parse(&encode(500, &[])).unwrap();
        assert!(c.samples.is_empty() && c.stats == Stats::default());
    }
}
