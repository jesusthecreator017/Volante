//! Deep-sleep cycle (docs/ble-profile.md "Sleep"): the pod has no switch, so 0x08 parks it and a timer wakes it
//! every `SLEEP_INTERVAL_S` to advertise for `SLEEP_ADV_MS`; 0x09 brings it back to the normal boot.

use core::time::Duration;
use esp_hal::peripherals::LPWR;
use esp_hal::rtc_cntl::{Rtc, SocResetReason, sleep::TimerWakeupSource};

pub const SLEEP_INTERVAL_S: u64 = 30;
pub const SLEEP_ADV_MS: u64 = 2000;
/// After a host connects while sleeping: how long it has to send 0x09
pub const SLEEP_CONNECT_S: u64 = 10;

const MAGIC: u32 = 0x05EE_9A11;

// RTC fast memory survives deep sleep and software reset, not a power loss (then the contents are garbage, which
// is why the reset reason is checked too)
#[esp_hal::ram(unstable(rtc_fast, persistent))]
static mut FLAG: u32 = 0;

fn flag(v: u32) {
    // SAFETY: single-threaded access to a plain u32 in RTC memory
    unsafe { (&raw mut FLAG).write_volatile(v) }
}

/// True on a timer wake from deep sleep entered by `enter`
pub fn is_sleeping() -> bool {
    // SAFETY: as above
    let f = unsafe { (&raw const FLAG).read_volatile() };
    f == MAGIC && esp_hal::system::reset_reason() == Some(SocResetReason::CoreDeepSleep)
}

pub fn enter(lpwr: LPWR<'_>) -> ! {
    flag(MAGIC);
    Rtc::new(lpwr).sleep_deep(&[&TimerWakeupSource::new(Duration::from_secs(SLEEP_INTERVAL_S))])
}

pub fn wake() -> ! {
    flag(0);
    esp_hal::system::software_reset()
}
