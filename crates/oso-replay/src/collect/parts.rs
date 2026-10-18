//! A session is recorded in parts: the pod's flash holds ~12.5 min at 500 Hz, a drills session takes 30-40 min. Between
//! blocks (never inside one) the part is stopped, uploaded with the verified path and a new one started. Pure decisions
//! live here; the bin does the BLE.

use super::blocks::{Record, Status};
use super::protocol::Block;
use super::session::Session;

/// Packets the pod's flash holds (374,784 = 12.5 min at 500 Hz)
pub const CAPACITY_PACKETS: u32 = 374_784;
/// Start a new part at the next block boundary once this fraction is stored
pub const SPLIT_FRACTION: f64 = 0.75;
/// ... or when the next block, at its expected length, would take the part past this fraction
pub const FIT_FRACTION: f64 = 0.95;
/// Expected seconds per rep, for blocks without a time (2 s of stillness between swings, slack for the player)
const SECS_PER_REP: u32 = 6;

pub fn part_file(index: u32) -> String {
    format!("imu.part{index}.bin")
}

/// Packets a block is expected to record
pub fn expected_packets(b: &Block) -> u32 {
    b.secs.unwrap_or(b.target * SECS_PER_REP) * u32::from(oso_proto::RATE_HZ)
}

/// Is it time for a new part? `stored`: packets in the pod's flash; `next_packets`: expected size of the block that would
/// run next; `blocks_in_part`: blocks finished in this part (including the one being ended); `every`: debug, a part per
/// N blocks. Never true when not `at_boundary` (a block is running): a full flash is dealt with when the block ends.
pub fn should_split(stored: u32, flash_full: bool, next_packets: u32, blocks_in_part: u32, every: Option<u32>, at_boundary: bool) -> bool {
    if !at_boundary {
        return false;
    }
    let cap = f64::from(CAPACITY_PACKETS);
    flash_full
        || f64::from(stored) >= SPLIT_FRACTION * cap
        || f64::from(stored) + f64::from(next_packets) >= FIT_FRACTION * cap
        || every.is_some_and(|n| n > 0 && blocks_in_part >= n)
}

/// First block with no done/redone/skipped record: where a resumed session continues. (A discarded attempt that was
/// never redone, or a block that was never reached, is still open.)
pub fn first_unfinished(total: usize, records: &[Record]) -> usize {
    (0..total)
        .find(|&i| !records.iter().any(|r| r.index == i && matches!(r.status, Status::Done | Status::Redone | Status::Skipped)))
        .unwrap_or(total)
}

/// The part number a new recording gets
pub fn next_part(s: &Session) -> u32 {
    let used = s.parts.iter().map(|p| p.index).chain(s.blocks.iter().map(|b| b.part)).chain(s.open_part).max();
    used.map_or(1, |m| m + 1)
}

#[derive(Debug, PartialEq, Eq)]
pub enum ResumePlan {
    /// The pod holds nothing of ours: start a new part
    Fresh,
    /// The pod still holds this part of the session: upload it into the folder first
    UploadOpen(u32),
    Refuse(String),
}

pub fn resume_plan(s: &Session, pod_pending: bool, pod_recording: bool) -> ResumePlan {
    if !pod_pending && !pod_recording {
        return ResumePlan::Fresh;
    }
    match s.open_part {
        Some(n) => ResumePlan::UploadOpen(n),
        None => ResumePlan::Refuse(
            "the pod holds a session that this one does not list as unfinished (it belongs to another session): upload it first with `oso-rec upload -o FILE`".into(),
        ),
    }
}

/// A block/marker time `t` on the clock of a part whose first packet is at `first` (both unwrapped from that packet): a
/// time before it can only be after the u32 microsecond clock wrapped (every 71.6 min), so it is on the next lap
pub fn unwrap_to(t: i64, first: i64) -> i64 {
    if t < first { t + (1 << 32) } else { t }
}

/// An uploaded file is the part we expect when every block that ran in it lies inside its time range
/// (`t_*` on the part's unwrapped clock, `first_raw` the raw first packet time)
pub fn part_matches(records: &[Record], part: u32, t_first: i64, t_last: i64) -> bool {
    records.iter().filter(|r| r.part == part).filter_map(|r| r.t_start_us).all(|t| (t_first..=t_last).contains(&unwrap_to(t, t_first)))
}

#[cfg(test)]
mod tests {
    use super::super::session::{MountAxes, Player, ProtocolRef, SCHEMA, Pod, PartRec};
    use super::*;

    const HALF: u32 = CAPACITY_PACKETS / 2;

    #[test]
    fn split_at_75_percent_flash_full_or_a_block_that_will_not_fit_but_never_mid_block() {
        let s = |stored, full, next, n, every, b| should_split(stored, full, next, n, every, b);
        assert!(!s(HALF, false, 50_000, 3, None, true));
        assert!(s(CAPACITY_PACKETS * 3 / 4, false, 0, 3, None, true));
        assert!(s(HALF, false, 120_000 * 2, 3, None, true), "free play would not fit");
        assert!(s(10, true, 0, 1, None, true), "flash_full");
        assert!(s(10, false, 0, 1, Some(1), true), "debug every block");
        assert!(!s(10, false, 0, 1, Some(2), true));
        // Mid-block: never, whatever the flash says
        assert!(!s(CAPACITY_PACKETS, true, 1_000_000, 9, Some(1), false));
    }

    #[test]
    fn expected_sizes_come_from_time_or_reps() {
        let b = |target, secs| Block { label: "clear".into(), side: Some("FH".into()), target, secs, instruction: "x".into(), optional: false, count_threshold_dps: None };
        assert_eq!(expected_packets(&b(0, Some(60))), 30_000);
        assert_eq!(expected_packets(&b(25, None)), 75_000);
    }

    fn rec(index: usize, part: u32, status: Status, t: Option<i64>) -> Record {
        Record { part, index, label: "clear".into(), side: Some("FH".into()), target: 25, counted: 0, t_start_us: t, t_end_us: t, status, note: None }
    }

    fn session(records: Vec<Record>, parts: u32, open: Option<u32>) -> Session {
        Session {
            schema: SCHEMA.into(),
            player: Player { id: "P07".into(), handed: None, level: None, height_bin: None },
            protocol: ProtocolRef { name: "drills-v1".into(), version: 1, seed: 77 },
            pod: Pod { mac: "m".into(), fw: "1.0".into() },
            mount: MountAxes { shaft: [0.0, 1.0, 0.0], normal: [0.0, 0.0, 1.0] },
            mount_calibrated_at: None,
            context: Default::default(),
            started_at: "2026-10-17T14:03:22Z".into(),
            parts: (1..=parts).map(|i| PartRec { index: i, file: part_file(i), packets: 1, t_first_us: 0, t_last_us: 1 }).collect(),
            open_part: open,
            blocks: records,
            markers: vec![],
            operator_notes: String::new(),
        }
    }

    #[test]
    fn resume_continues_with_the_next_block_the_same_order_and_a_new_part() {
        use super::super::blocks::{Key, Runner};
        use super::super::protocol::Protocol;
        let proto = Protocol::parse(super::super::protocol::builtin("drills-v1").unwrap()).unwrap();
        let blocks = proto.order(77);
        // First session: run 3 blocks, break after the second (a new part), crash in the third
        let mut r = Runner::new(blocks.clone());
        r.key(Key::Next, 0);
        r.key(Key::Next, 1_000_000);
        r.key(Key::Break, 2_000_000);
        r.set_part(2);
        r.key(Key::Next, 3_000_000);
        let kept: Vec<Record> = r.into_records();
        assert_eq!(kept.iter().map(|k| (k.index, k.part)).collect::<Vec<_>>(), [(0, 1), (1, 1)], "the crashed block never ended");
        let s = session(kept.clone(), 1, Some(2));
        assert_eq!(next_part(&s), 3, "part 2 was open: its number is taken");
        // Resumed: same seed, same order, continues at block 2 in a new part
        let again = Protocol::parse(super::super::protocol::builtin("drills-v1").unwrap()).unwrap().order(s.protocol.seed);
        assert_eq!(again, blocks);
        let mut r = Runner::resume(again, kept, next_part(&s));
        assert_eq!(r.view(0).idx, 2);
        assert!(!r.finished());
        r.key(Key::Next, 0);
        r.key(Key::Pause, 1);
        assert_eq!(r.records().last().map(|k| (k.index, k.part)), Some((2, 3)));
    }

    #[test]
    fn first_unfinished_skips_done_redone_and_skipped_but_not_discarded() {
        let recs = vec![rec(0, 1, Status::Done, Some(1)), rec(1, 1, Status::Skipped, None), rec(2, 1, Status::Discarded, Some(5))];
        assert_eq!(first_unfinished(5, &recs), 2);
        assert_eq!(first_unfinished(2, &recs), 2);
        assert_eq!(first_unfinished(3, &[]), 0);
        assert!(super::super::blocks::Runner::resume(vec![], vec![], 2).finished());
    }

    #[test]
    fn a_foreign_pending_session_is_refused_and_ours_is_uploaded_first() {
        let ours = session(vec![rec(0, 1, Status::Done, Some(100))], 0, Some(1));
        let closed = session(vec![rec(0, 1, Status::Done, Some(100))], 1, None);
        assert_eq!(resume_plan(&ours, false, false), ResumePlan::Fresh);
        assert_eq!(resume_plan(&ours, true, false), ResumePlan::UploadOpen(1));
        assert_eq!(resume_plan(&ours, false, true), ResumePlan::UploadOpen(1), "crashed while recording");
        assert!(matches!(resume_plan(&closed, true, false), ResumePlan::Refuse(_)));
        // The download is checked against the blocks of that part: a different session's recording fails
        assert!(part_matches(&ours.blocks, 1, 0, 1000));
        assert!(!part_matches(&ours.blocks, 1, 5_000_000, 9_000_000));
        assert_eq!(unwrap_to(5, 100), 5 + (1 << 32));
        assert_eq!(unwrap_to(150, 100), 150);
        assert!(part_matches(&ours.blocks, 2, 5_000_000, 9_000_000), "no blocks in that part: nothing to contradict");
    }
}
