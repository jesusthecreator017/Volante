//! The one contract between the racquet-pod firmware and the host (docs/ble-profile.md, docs/data-format.md).
//! `no_std`, no dependencies.

#![no_std]

mod session;
mod tap;
pub use session::*;
pub use tap::*;

pub const PACKET_LEN: usize = 18;
pub const RATE_HZ: u16 = 500;

/// One IMU sample, packet v1: `u16 seq, u32 t_us, i16 ax ay az gx gy gz`, little-endian.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Packet {
    pub seq: u16,
    pub t_us: u32,
    pub v: [i16; 6],
}

impl Packet {
    pub fn encode(&self) -> [u8; PACKET_LEN] {
        let mut o = [0u8; PACKET_LEN];
        o[..2].copy_from_slice(&self.seq.to_le_bytes());
        o[2..6].copy_from_slice(&self.t_us.to_le_bytes());
        for (i, c) in self.v.iter().enumerate() {
            o[6 + 2 * i..8 + 2 * i].copy_from_slice(&c.to_le_bytes());
        }
        o
    }

    pub fn decode(p: &[u8; PACKET_LEN]) -> Self {
        Packet {
            seq: u16::from_le_bytes([p[0], p[1]]),
            t_us: u32::from_le_bytes([p[2], p[3], p[4], p[5]]),
            v: core::array::from_fn(|i| i16::from_le_bytes([p[6 + 2 * i], p[7 + 2 * i]])),
        }
    }
}

/// GATT UUIDs (same bytes as the handoff app spec). Big-endian `u128`: `Uuid::from_u128` on the host,
/// `to_le_bytes()` for trouble.
pub mod uuid {
    pub const SERVICE: u128 = 0x6f736f00_0001_4c45_8a00_6261646d696e;
    pub const IMU_STREAM: u128 = 0x6f736f00_0002_4c45_8a00_6261646d696e;
    pub const CONTROL: u128 = 0x6f736f00_0003_4c45_8a00_6261646d696e;
    pub const STATUS: u128 = 0x6f736f00_0004_4c45_8a00_6261646d696e;
}

/// `control` characteristic opcodes (one byte).
pub mod op {
    pub const START_STREAM: u8 = 0x01;
    pub const STOP_STREAM: u8 = 0x02;
    pub const START_REC: u8 = 0x03;
    pub const STOP_REC: u8 = 0x04;
    pub const UPLOAD: u8 = 0x05;
    pub const MARK: u8 = 0x06;
    /// Host confirms a complete upload: sets the `uploaded` flag
    pub const MARK_UPLOADED: u8 = 0x07;
    /// Stop recording, put the IMU to sleep and enter deep sleep (timer wake every 30 s, see docs/ble-profile.md)
    pub const SLEEP: u8 = 0x08;
    /// Only honored while sleeping: back to the normal boot
    pub const WAKE: u8 = 0x09;
}

/// `status.state_bits`
pub mod state {
    pub const STREAMING: u8 = 1;
    pub const RECORDING: u8 = 2;
    pub const UPLOADING: u8 = 4;
    pub const PENDING_UPLOAD: u8 = 8;
    pub const FLASH_FULL: u8 = 16;
    pub const IMU_OK: u8 = 32;
    pub const IMU_IS_6500: u8 = 64;
    /// Not in the original table: pre-erasing flash, recording has not started yet
    pub const ERASING: u8 = 128;
}

/// `status.state_ext` (byte 20, was reserved)
pub mod state_ext {
    /// The pod is in its sleeping cycle: advertising briefly, only `op::WAKE` is honored
    pub const SLEEPING: u8 = 1;
}

/// `status.event`
pub mod event {
    pub const NONE: u8 = 0;
    pub const MARKER_TAP: u8 = 1;
    pub const MARKER_CMD: u8 = 2;
    pub const UPLOAD_DONE: u8 = 3;
    pub const ERROR: u8 = 4;
}

/// Error codes in `event_value` of an `event::ERROR`
pub mod err {
    pub const BAD_OPCODE: u32 = 1;
    pub const NO_SESSION: u32 = 2;
    pub const NEED_CONFIRM: u32 = 3;
    pub const BUSY: u32 = 4;
    /// Any opcode other than `op::WAKE` while sleeping
    pub const SLEEPING: u32 = 5;
}

pub const STATUS_LEN: usize = 24;

/// `status` characteristic: 23 packed little-endian bytes + 1 reserved zero byte.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Status {
    pub fw_major: u8,
    pub fw_minor: u8,
    pub rate_hz: u16,
    /// 255 = unknown
    pub battery_pct: u8,
    pub state_bits: u8,
    pub accel_g: u8,
    pub gyro_dps: u16,
    pub buffered_packets: u32,
    pub fifo_overflows: u16,
    pub event: u8,
    pub event_value: u32,
    /// `state_ext::*` bits
    pub state_ext: u8,
    /// Live-stream packets dropped on notify errors since the last connection
    pub stream_drops: u16,
}

impl Status {
    pub fn encode(&self) -> [u8; STATUS_LEN] {
        let mut o = [0u8; STATUS_LEN];
        o[0] = self.fw_major;
        o[1] = self.fw_minor;
        o[2..4].copy_from_slice(&self.rate_hz.to_le_bytes());
        o[4] = self.battery_pct;
        o[5] = self.state_bits;
        o[6] = self.accel_g;
        o[7..9].copy_from_slice(&self.gyro_dps.to_le_bytes());
        o[9..13].copy_from_slice(&self.buffered_packets.to_le_bytes());
        o[13..15].copy_from_slice(&self.fifo_overflows.to_le_bytes());
        o[15] = self.event;
        o[16..20].copy_from_slice(&self.event_value.to_le_bytes());
        o[20] = self.state_ext;
        o[21..23].copy_from_slice(&self.stream_drops.to_le_bytes());
        o
    }

    pub fn decode(b: &[u8]) -> Option<Self> {
        if b.len() < STATUS_LEN {
            return None;
        }
        Some(Status {
            fw_major: b[0],
            fw_minor: b[1],
            rate_hz: u16::from_le_bytes([b[2], b[3]]),
            battery_pct: b[4],
            state_bits: b[5],
            accel_g: b[6],
            gyro_dps: u16::from_le_bytes([b[7], b[8]]),
            buffered_packets: u32::from_le_bytes([b[9], b[10], b[11], b[12]]),
            fifo_overflows: u16::from_le_bytes([b[13], b[14]]),
            event: b[15],
            event_value: u32::from_le_bytes([b[16], b[17], b[18], b[19]]),
            state_ext: b[20],
            stream_drops: u16::from_le_bytes([b[21], b[22]]),
        })
    }
}

/// LiPo cell voltage (mV) to percent: piecewise-linear through a typical discharge curve, 4.20 V = 100, 3.30 V = 0
pub fn lipo_pct(mv: u32) -> u8 {
    const T: [(u32, u32); 11] = [
        (3300, 0), (3600, 5), (3700, 15), (3750, 25), (3790, 35), (3820, 45), (3850, 55), (3920, 65), (3980, 75),
        (4080, 85), (4200, 100),
    ];
    if mv <= T[0].0 {
        return 0;
    }
    for w in T.windows(2) {
        let ((v0, p0), (v1, p1)) = (w[0], w[1]);
        if mv <= v1 {
            return (p0 + (mv - v0) * (p1 - p0) / (v1 - v0)) as u8;
        }
    }
    100
}

#[cfg(test)]
extern crate std;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packet_roundtrip_and_layout() {
        let p = Packet { seq: 0x0102, t_us: 0x03040506, v: [1, -2, 2048, i16::MIN, i16::MAX, 0] };
        let b = p.encode();
        assert_eq!(&b[..6], &[2, 1, 6, 5, 4, 3]);
        assert_eq!(Packet::decode(&b), p);
    }

    #[test]
    fn lipo_curve_is_monotonic_and_clamped() {
        assert_eq!((lipo_pct(3000), lipo_pct(3300), lipo_pct(4200), lipo_pct(4500)), (0, 0, 100, 100));
        let mut last = 0;
        for mv in (3300..=4200).step_by(10) {
            let p = lipo_pct(mv);
            assert!(p >= last);
            last = p;
        }
    }

    #[test]
    fn status_roundtrip() {
        let s = Status {
            fw_major: 1, fw_minor: 2, rate_hz: 500, battery_pct: 255, state_bits: state::STREAMING | state::IMU_OK,
            accel_g: 16, gyro_dps: 2000, buffered_packets: 123_456, fifo_overflows: 7,
            event: event::UPLOAD_DONE, event_value: 0xdead_beef, state_ext: state_ext::SLEEPING, stream_drops: 0x0102,
        };
        let b = s.encode();
        assert_eq!(b.len(), 24);
        assert_eq!((b[20], &b[21..]), (1, &[2u8, 1, 0][..]));
        assert_eq!((op::SLEEP, op::WAKE, err::SLEEPING), (8, 9, 5));
        assert_eq!(Status::decode(&b), Some(s));
        assert_eq!(Status::decode(&b[..23]), None);
    }
}
