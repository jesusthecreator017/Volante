//! The block state machine of a collection session. No clock and no counter inside: the caller passes the device time
//! (`now_us`, the packets' clock) with every call and reports each counted swing, so tests fake both.
//!
//! States: *idle* (the next block is shown, nothing runs) and *active* (a block runs). Keys:
//! - next (space / n): ends the active block and starts the next one at once; from idle, starts it
//! - redo (r): the active attempt is thrown away (status `discarded`, data kept) and the block restarts; from idle,
//!   the block just ended is redone the same way
//! - skip (s): the active block ends as `skipped` (its times are kept); from idle the next block is skipped without
//!   times
//! - pause (p): ends the active block as done and goes idle: a stillness break with no label
//! - quit (q): ends the active block (noted "finished early") and the session
//!
//! Every block start and end asks for a pod marker (`Effect::Marker`). A finished attempt that followed a redo is
//! `redone`; a plain one `done`. Several records can share an `index`.

use super::protocol::Block;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Key {
    Next,
    Redo,
    Skip,
    Pause,
    Quit,
}

impl Key {
    pub fn from_byte(b: u8) -> Option<Key> {
        match b {
            b' ' | b'n' | b'N' => Some(Key::Next),
            b'r' | b'R' => Some(Key::Redo),
            b's' | b'S' => Some(Key::Skip),
            b'p' | b'P' => Some(Key::Pause),
            b'q' | b'Q' => Some(Key::Quit),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Effect {
    /// Send the pod a 0x06 marker (it glows)
    Marker,
    /// The block's target is reached (reps counted, or the timed block's time is up): ring the bell
    Bell,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Done,
    Skipped,
    /// Finished after an earlier attempt of the same block was redone
    Redone,
    /// Thrown away by `r`; its data stays in imu.bin
    Discarded,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Record {
    /// Position in the session's block order
    pub index: usize,
    pub label: String,
    pub side: Option<String>,
    pub target: u32,
    pub counted: u32,
    /// Device `t_us`; null for a block skipped before it started
    pub t_start_us: Option<i64>,
    pub t_end_us: Option<i64>,
    pub status: Status,
    pub note: Option<String>,
}

#[derive(Debug)]
struct Active {
    idx: usize,
    t_start: i64,
    counted: u32,
    belled: bool,
}

pub struct Runner {
    blocks: Vec<Block>,
    /// The block shown when idle
    next: usize,
    cur: Option<Active>,
    records: Vec<Record>,
    finished: bool,
}

/// What the screen needs
#[derive(Debug, PartialEq)]
pub struct View<'a> {
    /// Block number shown (0-based) and the total
    pub idx: usize,
    pub total: usize,
    pub block: Option<&'a Block>,
    pub active: bool,
    pub counted: u32,
    pub elapsed_s: f64,
    /// The target is reached (reps or time)
    pub done: bool,
}

impl Runner {
    pub fn new(blocks: Vec<Block>) -> Self {
        Self { blocks, next: 0, cur: None, records: Vec::new(), finished: false }
    }

    pub fn finished(&self) -> bool {
        self.finished
    }

    pub fn records(&self) -> &[Record] {
        &self.records
    }

    /// Live counter threshold (dps) for what is on screen: the active block's own, `default` otherwise;
    /// infinite when the block's counter is off (0)
    pub fn threshold(&self, default: f64) -> f64 {
        let t = self.cur.as_ref().and_then(|a| self.blocks[a.idx].count_threshold_dps).unwrap_or(default);
        if t > 0.0 { t } else { f64::INFINITY }
    }

    pub fn into_records(self) -> Vec<Record> {
        self.records
    }

    pub fn total_counted(&self) -> u32 {
        self.records.iter().filter(|r| r.status != Status::Discarded).map(|r| r.counted).sum()
    }

    fn start(&mut self, idx: usize, now: i64, out: &mut Vec<Effect>) {
        self.cur = Some(Active { idx, t_start: now, counted: 0, belled: false });
        out.push(Effect::Marker);
    }

    /// Ends the active block with `status`; returns its index
    fn end(&mut self, status: Status, note: Option<&str>, now: i64, out: &mut Vec<Effect>) -> Option<usize> {
        let a = self.cur.take()?;
        let b = &self.blocks[a.idx];
        let redone = self.records.iter().any(|r| r.index == a.idx);
        let status = if status == Status::Done && redone { Status::Redone } else { status };
        self.records.push(Record {
            index: a.idx,
            label: b.label.clone(),
            side: b.side.clone(),
            target: b.target,
            counted: a.counted,
            t_start_us: Some(a.t_start),
            t_end_us: Some(now),
            status,
            note: note.map(String::from),
        });
        out.push(Effect::Marker);
        self.next = a.idx + 1;
        Some(a.idx)
    }

    /// Starts block `self.next`, or finishes the session when there is none
    fn advance(&mut self, now: i64, out: &mut Vec<Effect>) {
        if self.next < self.blocks.len() {
            self.start(self.next, now, out);
        } else {
            self.finished = true;
        }
    }

    pub fn key(&mut self, key: Key, now: i64) -> Vec<Effect> {
        let mut out = Vec::new();
        if self.finished {
            return out;
        }
        match (key, self.cur.is_some()) {
            (Key::Next, true) => {
                self.end(Status::Done, None, now, &mut out);
                self.advance(now, &mut out);
            }
            (Key::Next, false) => self.advance(now, &mut out),
            (Key::Skip, true) => {
                self.end(Status::Skipped, None, now, &mut out);
                self.advance(now, &mut out);
            }
            (Key::Skip, false) => {
                if let Some(b) = self.blocks.get(self.next) {
                    self.records.push(Record {
                        index: self.next,
                        label: b.label.clone(),
                        side: b.side.clone(),
                        target: b.target,
                        counted: 0,
                        t_start_us: None,
                        t_end_us: None,
                        status: Status::Skipped,
                        note: None,
                    });
                    self.next += 1;
                    self.finished = self.next >= self.blocks.len();
                }
            }
            (Key::Redo, true) => {
                if let Some(idx) = self.end(Status::Discarded, Some("redone"), now, &mut out) {
                    self.next = idx;
                    self.advance(now, &mut out);
                }
            }
            (Key::Redo, false) => {
                // The block just ended, if it ran: its latest kept attempt is discarded and it starts again
                let prev = self.next.checked_sub(1);
                let last = self.records.iter_mut().rev().find(|r| Some(r.index) == prev && r.t_start_us.is_some() && r.status != Status::Discarded);
                if let Some(r) = last {
                    r.status = Status::Discarded;
                    r.note = Some("redone".into());
                    self.next = r.index;
                    self.advance(now, &mut out);
                }
            }
            (Key::Pause, true) => {
                self.end(Status::Done, None, now, &mut out);
                self.finished = self.next >= self.blocks.len();
            }
            (Key::Pause, false) => {}
            (Key::Quit, _) => {
                self.end(Status::Done, Some("finished early"), now, &mut out);
                self.finished = true;
            }
        }
        out
    }

    /// A swing was counted
    pub fn swing(&mut self) -> Vec<Effect> {
        let Some(a) = self.cur.as_mut() else { return Vec::new() };
        a.counted += 1;
        let target = self.blocks[a.idx].target;
        if target > 0 && a.counted >= target && !a.belled {
            a.belled = true;
            return vec![Effect::Bell];
        }
        Vec::new()
    }

    /// Time passes (a timed block rings when its time is up)
    pub fn tick(&mut self, now: i64) -> Vec<Effect> {
        let Some(a) = self.cur.as_mut() else { return Vec::new() };
        match self.blocks[a.idx].secs {
            Some(s) if !a.belled && (now - a.t_start) as f64 >= f64::from(s) * 1e6 => {
                a.belled = true;
                vec![Effect::Bell]
            }
            _ => Vec::new(),
        }
    }

    pub fn view(&self, now: i64) -> View<'_> {
        let total = self.blocks.len();
        match &self.cur {
            Some(a) => View {
                idx: a.idx,
                total,
                block: self.blocks.get(a.idx),
                active: true,
                counted: a.counted,
                elapsed_s: (now - a.t_start) as f64 * 1e-6,
                done: a.belled,
            },
            None => View { idx: self.next, total, block: self.blocks.get(self.next), active: false, counted: 0, elapsed_s: 0.0, done: false },
        }
    }
}

/// `m:ss`
pub fn clock(secs: f64) -> String {
    let s = secs.max(0.0) as u64;
    format!("{}:{:02}", s / 60, s % 60)
}

/// The short state line (window title and the terminal's first line)
pub fn headline(v: &View) -> String {
    let Some(b) = v.block else { return "all blocks done".into() };
    let what = format!("{}{}", b.label.to_uppercase(), b.side.as_deref().map_or(String::new(), |s| format!(" {s}")));
    let n = format!("block {}/{}", v.idx + 1, v.total);
    if !v.active {
        return format!("{n} {what}: press SPACE to start");
    }
    let goal = match b.secs {
        Some(s) => format!("{} / {}", clock(v.elapsed_s), clock(f64::from(s))),
        None => format!("{} / {} reps  {}", v.counted, b.target, clock(v.elapsed_s)),
    };
    format!("{n} {what} | {goal}{}", if v.done { " | DONE: press SPACE" } else { "" })
}

#[cfg(test)]
mod tests {
    use super::*;
    use Effect::{Bell, Marker};

    fn blk(label: &str, side: Option<&str>, target: u32, secs: Option<u32>) -> Block {
        Block { label: label.into(), side: side.map(String::from), target, secs, instruction: "x".into(), optional: false, count_threshold_dps: None }
    }

    /// clear FH x2, drop BH x3, not_a_hit 10 s
    fn runner() -> Runner {
        Runner::new(vec![blk("clear", Some("FH"), 2, None), blk("drop", Some("BH"), 3, None), blk("not_a_hit", None, 0, Some(10))])
    }

    const S: i64 = 1_000_000;

    #[test]
    fn next_walks_the_blocks_with_markers_and_finishes() {
        let mut r = runner();
        assert_eq!(r.key(Key::Next, S), [Marker]);
        assert_eq!(r.swing(), []);
        assert_eq!(r.swing(), [Bell], "target reached");
        assert_eq!(r.swing(), [], "one bell per attempt");
        assert!(r.view(5 * S).done && r.view(5 * S).counted == 3);
        assert_eq!(r.key(Key::Next, 10 * S), [Marker, Marker], "end + start");
        r.swing();
        assert_eq!(r.key(Key::Next, 20 * S), [Marker, Marker]);
        assert_eq!(r.key(Key::Next, 30 * S), [Marker]);
        assert!(r.finished());
        let rec = r.records();
        assert_eq!((rec[0].status, rec[0].counted, rec[0].t_start_us, rec[0].t_end_us), (Status::Done, 3, Some(S), Some(10 * S)));
        assert_eq!((rec[1].index, rec[1].counted, rec[1].t_start_us), (1, 1, Some(10 * S)));
        assert_eq!(rec[2].label, "not_a_hit");
        assert_eq!(r.total_counted(), 4);
        assert_eq!(r.key(Key::Next, 40 * S), [], "finished is final");
    }

    #[test]
    fn redo_discards_the_attempt_keeps_its_data_and_restarts() {
        let mut r = runner();
        r.key(Key::Next, 0);
        r.swing();
        assert_eq!(r.key(Key::Redo, 5 * S), [Marker, Marker]);
        r.swing();
        r.swing();
        r.key(Key::Next, 9 * S);
        let rec = r.records();
        assert_eq!((rec[0].status, rec[0].counted, rec[0].note.as_deref()), (Status::Discarded, 1, Some("redone")));
        assert_eq!((rec[0].t_start_us, rec[0].t_end_us), (Some(0), Some(5 * S)));
        assert_eq!((rec[1].index, rec[1].status, rec[1].counted, rec[1].t_start_us), (0, Status::Redone, 2, Some(5 * S)));
        assert_eq!(r.view(9 * S).idx, 1);
        assert_eq!(r.total_counted(), 2, "a discarded attempt does not count");
    }

    #[test]
    fn redo_from_idle_redoes_the_block_just_ended() {
        let mut r = runner();
        r.key(Key::Next, 0);
        r.swing();
        r.key(Key::Pause, 4 * S);
        assert!(!r.view(5 * S).active && r.view(5 * S).idx == 1);
        assert_eq!(r.key(Key::Redo, 6 * S), [Marker]);
        assert_eq!(r.records()[0].status, Status::Discarded);
        assert_eq!(r.view(7 * S).idx, 0);
        // Nothing to redo before any block ran
        let mut r = runner();
        assert_eq!(r.key(Key::Redo, 0), []);
    }

    #[test]
    fn skip_active_keeps_times_skip_idle_has_none() {
        let mut r = runner();
        r.key(Key::Next, 0);
        assert_eq!(r.key(Key::Skip, 3 * S), [Marker, Marker]);
        assert_eq!((r.records()[0].status, r.records()[0].t_end_us), (Status::Skipped, Some(3 * S)));
        r.key(Key::Pause, 4 * S);
        assert_eq!(r.key(Key::Skip, 5 * S), [], "idle skip: no marker");
        let k = &r.records()[2];
        assert_eq!((k.index, k.status, k.t_start_us, k.t_end_us), (2, Status::Skipped, None, None));
        assert!(r.finished(), "the last block was skipped");
    }

    #[test]
    fn pause_is_a_labelless_break_and_next_resumes() {
        let mut r = runner();
        r.key(Key::Next, 0);
        r.swing();
        assert_eq!(r.key(Key::Pause, 2 * S), [Marker]);
        assert!(!r.finished() && !r.view(3 * S).active);
        assert_eq!(r.swing(), [], "swings in a break count for nothing");
        assert_eq!(r.key(Key::Pause, 3 * S), []);
        assert_eq!(r.key(Key::Next, 60 * S), [Marker]);
        assert_eq!(r.view(61 * S).idx, 1);
        assert_eq!(r.records().len(), 1);
    }

    #[test]
    fn quit_ends_the_active_block_with_a_note() {
        let mut r = runner();
        r.key(Key::Next, 0);
        assert_eq!(r.key(Key::Quit, 2 * S), [Marker]);
        assert!(r.finished());
        assert_eq!(r.records()[0].note.as_deref(), Some("finished early"));
        let mut r = runner();
        assert_eq!(r.key(Key::Quit, 0), []);
        assert!(r.finished() && r.records().is_empty());
    }

    #[test]
    fn timed_blocks_ring_when_the_time_is_up() {
        let mut r = Runner::new(vec![blk("not_a_hit", None, 0, Some(10))]);
        r.key(Key::Next, 100 * S);
        assert_eq!(r.tick(105 * S), []);
        assert_eq!(r.tick(110 * S), [Bell]);
        assert_eq!(r.tick(111 * S), []);
        let h = headline(&r.view(112 * S));
        assert!(h.contains("NOT_A_HIT") && h.contains("0:12 / 0:10") && h.contains("DONE"), "{h}");
    }

    #[test]
    fn threshold_follows_the_active_block() {
        let mut b = vec![blk("clear", Some("FH"), 2, None), blk("not_a_hit", None, 0, Some(10))];
        b[0].count_threshold_dps = Some(700.0);
        b[1].count_threshold_dps = Some(0.0);
        let mut r = Runner::new(b);
        assert_eq!(r.threshold(400.0), 400.0, "idle: default");
        r.key(Key::Next, 0);
        assert_eq!(r.threshold(400.0), 700.0);
        r.key(Key::Next, S);
        assert_eq!(r.threshold(400.0), f64::INFINITY, "0 = off");
    }

    #[test]
    fn headline_shows_block_label_side_reps_and_time() {
        let mut r = runner();
        assert_eq!(headline(&r.view(0)), "block 1/3 CLEAR FH: press SPACE to start");
        r.key(Key::Next, 0);
        r.swing();
        assert_eq!(headline(&r.view(65 * S)), "block 1/3 CLEAR FH | 1 / 2 reps  1:05");
        assert_eq!(Key::from_byte(b' '), Some(Key::Next));
        assert_eq!(Key::from_byte(b'x'), None);
    }
}
