//! One recorded session in the `imu` data partition: sector 0 is the record log (oso_proto::session), packets follow
//! from sector 1. The whole area is erased before recording starts, so sampling only ever does small page programs.

use crate::shared::{CMD, Cmd, EVENTS, MARKER_BLINKS, OVERFLOWS, REC, STORED, UPLOAD_OK, has, set};
use embassy_futures::select::{Either3, select3};
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex as Cs;
use embassy_sync::mutex::Mutex;
use embassy_time::{Duration, Instant, Ticker, Timer};
use embedded_storage::nor_flash::{NorFlash, ReadNorFlash};
use esp_storage::FlashStorage;
use oso_proto::{
    DATA_START, LOG_BYTES, MAX_MARKERS, Marker, PACKET_LEN, Packet, RATE_HZ, RECORD_LEN, SRC_TAP, SessionInfo, err,
    event, kind, record, state as bits,
};
use core::sync::atomic::Ordering::Relaxed;

// ponytail: hardcoded to partitions.csv (imu, data, spiffs); read the table via esp-bootloader if it ever moves
const BASE: u32 = 0x190000;
const SIZE: u32 = 0x670000;
const BLOCK: u32 = 64 * 1024;
/// 14 packets = 252 B, a multiple of 4 (flash words) and close to a 256 B page
const STAGE_PKTS: usize = 14;
const STAGE: usize = STAGE_PKTS * PACKET_LEN;
const CAPACITY: u32 = (SIZE - DATA_START) / PACKET_LEN as u32;

/// The ROM flash routines need word-aligned buffers
#[repr(align(4))]
struct Al<const N: usize>([u8; N]);

pub struct Store {
    flash: FlashStorage<'static>,
    pub info: SessionInfo,
    next_slot: usize,
    /// Packets in flash
    written: u32,
    stage: Al<STAGE>,
    staged: usize,
    progress_logged: u32,
}

impl Store {
    pub fn load(mut flash: FlashStorage<'static>) -> Self {
        let mut log = alloc::vec![0u32; LOG_BYTES / 4];
        // SAFETY: a u32 buffer viewed as bytes (same size, weaker alignment)
        let bytes = unsafe { core::slice::from_raw_parts_mut(log.as_mut_ptr().cast::<u8>(), LOG_BYTES) };
        if let Err(e) = flash.read(BASE, bytes) {
            log::error!("flash read: {e:?}");
        }
        let (info, next_slot) = SessionInfo::fold(bytes);
        let written = info.count.min(CAPACITY);
        Store { flash, info, next_slot, written, stage: Al([0; STAGE]), staged: 0, progress_logged: written }
    }

    /// A session with data that was not uploaded yet: never overwritten silently
    pub fn needs_confirm(&self) -> bool {
        self.info.started && !self.info.uploaded && self.info.count > 0
    }

    /// Boot rule: overwrite only an empty or uploaded session. Returns true when a new recording started.
    pub async fn boot(&mut self) -> bool {
        if !self.needs_confirm() {
            self.erase_and_start().await;
            return true;
        }
        if !self.info.complete {
            // Powered off mid-session: the log's last progress record is the count
            self.append(kind::COMPLETE, 0, self.info.count);
            self.info.complete = true;
        }
        STORED.store(self.info.count, Relaxed);
        set(bits::PENDING_UPLOAD, true);
        false
    }

    fn append(&mut self, k: u8, aux: u8, v: u32) -> bool {
        if self.next_slot >= oso_proto::LOG_SLOTS {
            return false;
        }
        let r = Al(record(k, aux, v));
        let ok = self.flash.write(BASE + (self.next_slot * RECORD_LEN) as u32, &r.0).is_ok();
        if ok {
            self.next_slot += 1;
        } else {
            log::error!("flash log write failed");
        }
        ok
    }

    /// Erases the whole area (the log sector first, so a power cut mid-erase leaves "no session" and the next boot
    /// erases again) in 64 KB blocks, yielding in between, then writes START.
    pub async fn erase_and_start(&mut self) {
        set(bits::ERASING, true);
        let t0 = Instant::now();
        let end = BASE + SIZE;
        if let Err(e) = self.flash.erase(BASE, BASE + DATA_START) {
            log::error!("flash log erase: {e:?}");
        }
        let mut addr = BASE + DATA_START;
        while addr < end {
            let next = ((addr / BLOCK + 1) * BLOCK).min(end);
            if let Err(e) = self.flash.erase(addr, next) {
                log::error!("flash erase {addr:#x}: {e:?}");
            }
            addr = next;
            Timer::after_millis(1).await;
        }
        log::info!("flash erased in {} ms", t0.elapsed().as_millis());
        self.info = SessionInfo { started: true, rate_hz: RATE_HZ, ..SessionInfo::default() };
        self.next_slot = 0;
        self.written = 0;
        self.staged = 0;
        self.progress_logged = 0;
        STORED.store(0, Relaxed);
        self.append(kind::START, 0, RATE_HZ as u32);
        set(bits::ERASING, false);
    }

    /// False when the area is full
    pub fn push(&mut self, p: &Packet) -> bool {
        if self.written + (self.staged / PACKET_LEN) as u32 >= CAPACITY {
            return false;
        }
        self.stage.0[self.staged..self.staged + PACKET_LEN].copy_from_slice(&p.encode());
        self.staged += PACKET_LEN;
        if self.staged == STAGE {
            self.flush();
        }
        true
    }

    fn flush(&mut self) {
        if self.staged == 0 {
            return;
        }
        let len = self.staged.next_multiple_of(4);
        self.stage.0[self.staged..len].fill(0xFF);
        let off = BASE + DATA_START + self.written * PACKET_LEN as u32;
        match self.flash.write(off, &self.stage.0[..len]) {
            Ok(()) => {
                self.written += (self.staged / PACKET_LEN) as u32;
                STORED.store(self.written, Relaxed);
            }
            Err(e) => log::error!("flash write {off:#x}: {e:?}"),
        }
        self.staged = 0;
    }

    /// Re-writes the durable packet count (every ~2 s) so a power cut loses little
    pub fn progress(&mut self) {
        // Keep the last slots for COMPLETE / UPLOADED
        if self.written != self.progress_logged && self.next_slot < oso_proto::LOG_SLOTS - 3 {
            self.append(kind::PROGRESS, 0, self.written);
            self.progress_logged = self.written;
            self.info.count = self.written;
        }
    }

    pub fn stop(&mut self) {
        self.flush();
        self.append(kind::COMPLETE, 0, self.written);
        self.info.count = self.written;
        self.info.complete = true;
    }

    fn marker(&mut self, m: Marker) {
        if self.info.n_markers < MAX_MARKERS && self.append(kind::MARKER, m.source, m.t_us) {
            self.info.markers[self.info.n_markers] = m;
            self.info.n_markers += 1;
        }
    }

    fn mark_uploaded(&mut self) {
        if self.append(kind::UPLOADED, 0, 0) {
            self.info.uploaded = true;
        }
    }

    /// `n` packets from packet index `first` into `out` (n * 18 bytes)
    pub fn read_packets(&mut self, first: u32, n: usize, out: &mut [u8]) -> bool {
        let off = DATA_START + first * PACKET_LEN as u32;
        let start = off & !3;
        let end = (off + (n * PACKET_LEN) as u32).next_multiple_of(4);
        let mut w = Al([0u8; 248]);
        let len = (end - start) as usize;
        if len > w.0.len() || self.flash.read(BASE + start, &mut w.0[..len]).is_err() {
            return false;
        }
        let skip = (off - start) as usize;
        out[..n * PACKET_LEN].copy_from_slice(&w.0[skip..skip + n * PACKET_LEN]);
        true
    }
}

fn error(code: u32) {
    let _ = EVENTS.try_send((event::ERROR, code));
}

async fn next_packet() -> Packet {
    if has(bits::RECORDING) {
        REC.receive().await
    } else {
        core::future::pending().await
    }
}

fn refresh_pending(s: &Store) {
    set(bits::PENDING_UPLOAD, !has(bits::RECORDING) && s.needs_confirm());
}

#[embassy_executor::task]
pub async fn run(store: &'static Mutex<Cs, Store>) {
    let mut progress = Ticker::every(Duration::from_secs(2));
    let mut confirm_until: Option<Instant> = None;
    loop {
        match select3(CMD.receive(), next_packet(), progress.next()).await {
            Either3::First(cmd) => {
                let mut s = store.lock().await;
                match cmd {
                    Cmd::StartRec => {
                        if has(bits::RECORDING) || has(bits::UPLOADING) {
                            // Already recording, or busy sending the session
                        } else if s.needs_confirm() && !confirm_until.is_some_and(|t| Instant::now() < t) {
                            confirm_until = Some(Instant::now() + Duration::from_secs(5));
                            error(err::NEED_CONFIRM);
                        } else {
                            confirm_until = None;
                            set(bits::PENDING_UPLOAD, false);
                            s.erase_and_start().await;
                            REC.clear();
                            OVERFLOWS.store(0, Relaxed);
                            set(bits::FLASH_FULL, false);
                            set(bits::RECORDING, true);
                        }
                    }
                    Cmd::StopRec => {
                        stop(&mut s);
                    }
                    Cmd::MarkUploaded => {
                        if has(bits::RECORDING) || !s.info.started || s.info.uploaded || !UPLOAD_OK.swap(false, Relaxed) {
                            error(err::NO_SESSION);
                        } else {
                            s.mark_uploaded();
                            refresh_pending(&s);
                        }
                    }
                    Cmd::Marker(m) => {
                        let ev = if m.source == SRC_TAP { event::MARKER_TAP } else { event::MARKER_CMD };
                        // Live events would mix into the markers an upload replays
                        if !has(bits::UPLOADING) {
                            let _ = EVENTS.try_send((ev, m.t_us));
                        }
                        MARKER_BLINKS.fetch_add(1, Relaxed);
                        if has(bits::RECORDING) {
                            s.marker(m);
                        }
                    }
                }
            }
            Either3::Second(p) => {
                let mut s = store.lock().await;
                if !s.push(&p) {
                    // Full: stop recording, streaming goes on
                    set(bits::FLASH_FULL, true);
                    stop(&mut s);
                }
            }
            Either3::Third(()) => {
                if has(bits::RECORDING) {
                    store.lock().await.progress();
                }
            }
        }
    }
}

/// Ends the recording: drains the ring into flash, then writes COMPLETE
fn stop(s: &mut Store) {
    if !has(bits::RECORDING) {
        return;
    }
    set(bits::RECORDING, false);
    while let Ok(p) = REC.try_receive() {
        if !s.push(&p) {
            break;
        }
    }
    s.stop();
    refresh_pending(s);
}

pub fn lock_new(s: Store) -> &'static Mutex<Cs, Store> {
    static CELL: static_cell::StaticCell<Mutex<Cs, Store>> = static_cell::StaticCell::new();
    CELL.init(Mutex::new(s))
}
