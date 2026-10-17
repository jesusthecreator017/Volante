//! State shared between the sampler, store, BLE and LED tasks.

use core::sync::atomic::{AtomicBool, AtomicU8, AtomicU16, AtomicU32, Ordering::Relaxed};
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex as Cs;
use embassy_sync::channel::Channel;
use oso_proto::{Marker, Packet};

/// Live stream to BLE. Lossy: the sampler drops the oldest packet when full
pub static STREAM: Channel<Cs, Packet, 512> = Channel::new();
/// Packets for the flash recorder: ~32 KB, lossless as long as the store keeps up
pub static REC: Channel<Cs, Packet, 1600> = Channel::new();
/// Status events for BLE: (event, value)
pub static EVENTS: Channel<Cs, (u8, u32), 8> = Channel::new();
pub static CMD: Channel<Cs, Cmd, 8> = Channel::new();

pub enum Cmd {
    StartRec,
    StopRec,
    MarkUploaded,
    Marker(Marker),
}

pub static STATE: AtomicU8 = AtomicU8::new(0);
pub static OVERFLOWS: AtomicU16 = AtomicU16::new(0);
/// Live-stream packets dropped on notify errors since the last connect (status bytes 21..23)
pub static STREAM_DROPS: AtomicU16 = AtomicU16::new(0);
/// Notify errors since boot (diagnostics)
pub static NOTIFY_ERRS: AtomicU32 = AtomicU32::new(0);
/// A host is connected (heartbeat)
pub static CONNECTED: AtomicBool = AtomicBool::new(false);
/// Last host write or successful data notify, ms since boot (idle-link watchdog)
pub static LAST_ACT_MS: AtomicU32 = AtomicU32::new(0);
/// Packets in the stored session (durably written)
pub static STORED: AtomicU32 = AtomicU32::new(0);
pub static BATTERY: AtomicU8 = AtomicU8::new(255);
/// Set by the BLE task on control 0x05, cleared by it when the upload ends
pub static UPLOAD_REQ: AtomicU8 = AtomicU8::new(0);
/// True after a full upload_done was sent; the host's 0x07 is only honored then
pub static UPLOAD_OK: core::sync::atomic::AtomicBool = core::sync::atomic::AtomicBool::new(false);
/// Counts LED blink-twice requests (marker feedback)
pub static MARKER_BLINKS: AtomicU8 = AtomicU8::new(0);

/// `oso_proto::state_ext` bits
pub static STATE_EXT: AtomicU8 = AtomicU8::new(0);
/// Set by the BLE task on control 0x08 (recording already stopped); main then sleeps the IMU and enters deep sleep
pub static SLEEP_REQ: AtomicBool = AtomicBool::new(false);
/// The sampler put the IMU to sleep
pub static IMU_ASLEEP: embassy_sync::signal::Signal<Cs, ()> = embassy_sync::signal::Signal::new();

pub fn set(bit: u8, on: bool) {
    if on {
        STATE.fetch_or(bit, Relaxed);
    } else {
        STATE.fetch_and(!bit, Relaxed);
    }
}

pub fn has(bit: u8) -> bool {
    STATE.load(Relaxed) & bit != 0
}

/// Milliseconds since boot, low 32 bits
pub fn now_ms() -> u32 {
    esp_hal::time::Instant::now().duration_since_epoch().as_millis() as u32
}

/// Device clock in us, low 32 bits (the packet `t_us`)
pub fn now_us() -> u32 {
    esp_hal::time::Instant::now().duration_since_epoch().as_micros() as u32
}

/// Lossy send: drop the oldest when full
pub fn push_lossy<const N: usize>(ch: &Channel<Cs, Packet, N>, p: Packet) {
    if ch.try_send(p).is_err() {
        let _ = ch.try_receive();
        let _ = ch.try_send(p);
    }
}
