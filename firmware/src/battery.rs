//! Battery level. The pod has no sense pin yet, so the level reports 255 (unknown) until the divider exists.

use crate::shared::BATTERY;
use core::sync::atomic::Ordering::Relaxed;
use embassy_time::Timer;
use esp_hal::analog::adc::{Adc, AdcConfig, Attenuation};
use esp_hal::peripherals::{ADC1, GPIO1};

// ponytail: flip after soldering 2x 220 kOhm BAT+ -> A0 (GPIO1) -> GND. The ADC is uncalibrated (full scale taken as
// 3.1 V at 11 dB), so expect a few percent of error until esp-hal's curve calibration is wired in.
pub const HAS_BATTERY_DIVIDER: bool = false;

#[embassy_executor::task]
pub async fn run(adc1: ADC1<'static>, pin: GPIO1<'static>) {
    let mut cfg = AdcConfig::new();
    let mut pin = cfg.enable_pin(pin, Attenuation::_11dB);
    let mut adc = Adc::new(adc1, cfg);
    loop {
        let raw: u32 = (0..16).map(|_| adc.read_blocking(&mut pin) as u32).sum::<u32>() / 16;
        // x2 for the divider
        let mv = raw * 3100 / 4095 * 2;
        BATTERY.store(oso_proto::lipo_pct(mv), Relaxed);
        Timer::after_secs(10).await;
    }
}
