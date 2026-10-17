//! Flash session log. Flash only clears bits, and an erase during sampling would stall for up to 400 ms, so the
//! "session header" is an append-only log of 8-byte records in sector 0 of the data area (pre-erased to 0xFF with the
//! rest of it); packets follow from sector 1 (`DATA_START`). The header is the fold of the records.

pub const RECORD_LEN: usize = 8;
pub const LOG_BYTES: usize = 4096;
pub const LOG_SLOTS: usize = LOG_BYTES / RECORD_LEN;
/// First packet byte, relative to the data partition
pub const DATA_START: u32 = LOG_BYTES as u32;
pub const MAX_MARKERS: usize = 32;

pub mod kind {
    /// value = rate_hz
    pub const START: u8 = 1;
    /// value = packets durably written so far (written every ~2 s)
    pub const PROGRESS: u8 = 2;
    /// aux = marker source, value = t_us
    pub const MARKER: u8 = 3;
    /// value = final packet count
    pub const COMPLETE: u8 = 4;
    pub const UPLOADED: u8 = 5;
}

pub const SRC_TAP: u8 = 1;
pub const SRC_CMD: u8 = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Marker {
    pub t_us: u32,
    pub source: u8,
}

pub fn record(kind: u8, aux: u8, value: u32) -> [u8; RECORD_LEN] {
    let v = value.to_le_bytes();
    [kind, kind ^ aux ^ v[0] ^ v[1] ^ v[2] ^ v[3] ^ 0xA5, aux, 0, v[0], v[1], v[2], v[3]]
}

fn valid(r: &[u8]) -> bool {
    r[3] == 0 && r[1] == r[0] ^ r[2] ^ r[4] ^ r[5] ^ r[6] ^ r[7] ^ 0xA5
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SessionInfo {
    pub started: bool,
    pub rate_hz: u16,
    pub count: u32,
    pub markers: [Marker; MAX_MARKERS],
    pub n_markers: usize,
    pub complete: bool,
    pub uploaded: bool,
}

impl SessionInfo {
    /// Folds the log sector. Returns the session and the first blank slot (where the next record goes; `LOG_SLOTS`
    /// when full). A torn or corrupt record is skipped. A log whose first record is not a START is no session.
    pub fn fold(log: &[u8]) -> (Self, usize) {
        let mut s = SessionInfo::default();
        let mut next = LOG_SLOTS;
        for (i, r) in log.as_chunks::<RECORD_LEN>().0.iter().take(LOG_SLOTS).enumerate() {
            if r.iter().all(|&b| b == 0xFF) {
                next = i;
                break;
            }
            if !valid(r) {
                continue;
            }
            let v = u32::from_le_bytes([r[4], r[5], r[6], r[7]]);
            match r[0] {
                kind::START => {
                    s = SessionInfo { started: true, rate_hz: v as u16, ..SessionInfo::default() };
                }
                _ if !s.started => {}
                kind::PROGRESS => s.count = s.count.max(v),
                kind::COMPLETE => {
                    s.count = s.count.max(v);
                    s.complete = true;
                }
                kind::UPLOADED => s.uploaded = true,
                kind::MARKER if s.n_markers < MAX_MARKERS => {
                    s.markers[s.n_markers] = Marker { t_us: v, source: r[2] };
                    s.n_markers += 1;
                }
                _ => {}
            }
        }
        (s, next)
    }

    pub fn markers(&self) -> &[Marker] {
        &self.markers[..self.n_markers]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::vec::Vec;

    fn log(recs: &[[u8; RECORD_LEN]]) -> Vec<u8> {
        let mut l = std::vec![0xFFu8; LOG_BYTES];
        for (i, r) in recs.iter().enumerate() {
            l[i * 8..i * 8 + 8].copy_from_slice(r);
        }
        l
    }

    #[test]
    fn blank_log_is_no_session() {
        let (s, next) = SessionInfo::fold(&log(&[]));
        assert!(!s.started && next == 0);
    }

    #[test]
    fn folds_progress_markers_and_flags() {
        let l = log(&[
            record(kind::START, 0, 500),
            record(kind::PROGRESS, 0, 1000),
            record(kind::MARKER, SRC_TAP, 777),
            record(kind::PROGRESS, 0, 2000),
            record(kind::MARKER, SRC_CMD, 888),
        ]);
        let (s, next) = SessionInfo::fold(&l);
        assert_eq!((s.started, s.rate_hz, s.count, next), (true, 500, 2000, 5));
        assert_eq!(s.markers(), &[Marker { t_us: 777, source: SRC_TAP }, Marker { t_us: 888, source: SRC_CMD }]);
        assert!(!s.complete && !s.uploaded);
        let mut l = l;
        l[40..48].copy_from_slice(&record(kind::COMPLETE, 0, 2500));
        l[48..56].copy_from_slice(&record(kind::UPLOADED, 0, 0));
        let (s, next) = SessionInfo::fold(&l);
        assert!(s.complete && s.uploaded && s.count == 2500 && next == 7);
    }

    #[test]
    fn torn_record_is_skipped_and_marker_cap_holds() {
        let mut recs = std::vec![record(kind::START, 0, 500), record(kind::PROGRESS, 0, 10)];
        recs[1][5] ^= 1;
        let (s, next) = SessionInfo::fold(&log(&recs));
        assert!(s.count == 0 && next == 2);
        let mut many = std::vec![record(kind::START, 0, 500)];
        many.extend((0..40).map(|i| record(kind::MARKER, SRC_TAP, i)));
        let (s, _) = SessionInfo::fold(&log(&many));
        assert_eq!(s.n_markers, MAX_MARKERS);
    }

    #[test]
    fn new_start_replaces_and_garbage_is_not_a_session() {
        let (s, _) = SessionInfo::fold(&log(&[record(kind::PROGRESS, 0, 5), record(kind::UPLOADED, 0, 0)]));
        assert!(!s.started && !s.uploaded && s.count == 0);
        let (s, _) = SessionInfo::fold(&std::vec![0x5Au8; LOG_BYTES]);
        assert!(!s.started);
    }
}
