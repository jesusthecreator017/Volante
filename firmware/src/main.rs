#![no_std]
#![no_main]
#![deny(
    clippy::mem_forget,
    reason = "mem::forget is generally not safe to do with esp_hal types, especially those \
    holding buffers for the duration of a data transfer."
)]
// Async fn futures live in the executor's task arena, not on the stack; the lint counts their state as stack
#![allow(clippy::large_stack_frames)]

mod battery;
mod ble;
mod imu;
mod sampler;
mod shared;
mod sleep;
mod store;

use bt_hci::controller::ExternalController;
use core::sync::atomic::Ordering::Relaxed;
use embassy_executor::Spawner;
use embassy_futures::select::select;
use embassy_time::{Duration, Instant, Timer, with_timeout};
use esp_backtrace as _;
use esp_hal::clock::CpuClock;
use esp_hal::gpio::{Level, Output, OutputConfig};
use esp_hal::i2c::master::{Config as I2cConfig, I2c};
use esp_hal::time::Rate;
use esp_hal::timer::timg::TimerGroup;
use esp_radio::ble::controller::BleConnector;
use log::info;
use oso_proto::state as bits;
use shared::{IMU_ASLEEP, MARKER_BLINKS, SLEEP_REQ, STATE_EXT, has, set};

extern crate alloc;

// This creates a default app-descriptor required by the esp-idf bootloader.
// For more information see: <https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/system/app_image_format.html#application-description>
esp_bootloader_esp_idf::esp_app_desc!();

const SYNC_GLOW_MS: u64 = 1200;
const SYNC_GLOW_OFF_MS: u64 = 300;

/// User LED (GPIO21, active low), highest priority first: fast blink = IMU error, slow = erasing flash, solid 1.2 s
/// then 0.3 s off = sync tap or marker registered (a second one inside the glow restarts the 1.2 s), double blink =
/// un-uploaded session waiting. Debug only.
#[embassy_executor::task]
async fn led(mut led: Output<'static>) {
    async fn blink(led: &mut Output<'static>, on_ms: u64, off_ms: u64) {
        led.set_low();
        Timer::after_millis(on_ms).await;
        led.set_high();
        Timer::after_millis(off_ms).await;
    }
    loop {
        if !has(bits::IMU_OK) {
            blink(&mut led, 100, 100).await;
        } else if has(bits::ERASING) {
            blink(&mut led, 500, 500).await;
        } else if MARKER_BLINKS.load(Relaxed) > 0 {
            info!("sync glow");
            let mut end = Instant::now() + Duration::from_millis(SYNC_GLOW_MS);
            led.set_low();
            while Instant::now() < end {
                if MARKER_BLINKS.swap(0, Relaxed) > 0 {
                    end = Instant::now() + Duration::from_millis(SYNC_GLOW_MS);
                }
                Timer::after_millis(20).await;
            }
            led.set_high();
            Timer::after_millis(SYNC_GLOW_OFF_MS).await;
        } else if has(bits::PENDING_UPLOAD) {
            blink(&mut led, 80, 150).await;
            blink(&mut led, 80, 1500).await;
        } else {
            Timer::after_millis(50).await;
        }
    }
}

#[esp_rtos::main]
async fn main(spawner: Spawner) -> ! {
    esp_println::logger::init_logger_from_env();

    let sleeping = sleep::is_sleeping();
    // The sleeping cycle only advertises: the lowest clock the radio runs at
    let clock = if sleeping { CpuClock::_80MHz } else { CpuClock::max() };
    let peripherals = esp_hal::init(esp_hal::Config::default().with_cpu_clock(clock));

    esp_alloc::heap_allocator!(#[esp_hal::ram(reclaimed)] size: 73744);

    let timg0 = TimerGroup::new(peripherals.TIMG0);
    let sw_interrupt =
        esp_hal::interrupt::software::SoftwareInterruptControl::new(peripherals.SW_INTERRUPT);
    esp_rtos::start(timg0.timer0, sw_interrupt.software_interrupt0);

    if sleeping {
        sleeping_cycle(peripherals.BT, peripherals.LPWR).await
    }

    // The USB serial port re-enumerates on reset (~3 s on macOS); wait so the boot log is not lost
    Timer::after_millis(3500).await;
    info!("oso-fw {}.{} up", 0, 1);
    spawner.spawn(led(Output::new(peripherals.GPIO21, Level::High, OutputConfig::default())).unwrap());

    // IMU: D4 = GPIO5 (SDA), D5 = GPIO6 (SCL), address 0x68 (AD0 floating)
    let i2c = I2c::new(peripherals.I2C0, I2cConfig::default().with_frequency(Rate::from_khz(400)))
        .unwrap()
        .with_sda(peripherals.GPIO5)
        .with_scl(peripherals.GPIO6)
        .into_async();
    let mut imu = imu::Imu::new(i2c);
    match imu.init().await {
        Ok(Some((chip, id))) => {
            info!("IMU {chip:?} (WHO_AM_I {id:#04x}) configured: 500 Hz, +-16 g, +-2000 dps");
            set(bits::IMU_OK, true);
            set(bits::IMU_IS_6500, chip == imu::Chip::Mpu6500);
            if chip == imu::Chip::Mpu6500 {
                log::warn!("MPU-6500 clone (no magnetometer), fine for oso");
            }
        }
        Ok(None) => log::error!("unknown WHO_AM_I: not an MPU-9250"),
        Err(e) => {
            log::error!("IMU not answering on I2C: {e:?}");
            imu.scan().await;
        }
    }

    // Flash session: a recording starts by itself unless an un-uploaded session is waiting
    let mut st = store::Store::load(esp_storage::FlashStorage::new(peripherals.FLASH));
    info!(
        "flash session: started={} packets={} complete={} uploaded={} markers={}",
        st.info.started, st.info.count, st.info.complete, st.info.uploaded, st.info.n_markers
    );
    let recording = st.boot().await;
    set(bits::RECORDING, recording);
    info!("{}", if recording { "recording" } else { "un-uploaded session kept: not recording (0x03 twice to overwrite)" });
    let store = store::lock_new(st);
    spawner.spawn(store::run(store).unwrap());

    if battery::HAS_BATTERY_DIVIDER {
        spawner.spawn(battery::run(peripherals.ADC1, peripherals.GPIO1).unwrap());
    }
    if has(bits::IMU_OK) {
        spawner.spawn(sampler::run(imu).unwrap());
    } else if cfg!(feature = "sim") {
        #[cfg(feature = "sim")]
        spawner.spawn(sampler::sim().unwrap());
        log::warn!("no IMU: running the synthetic sampler");
    }

    let mac = esp_hal::efuse::interface_mac_address(esp_hal::efuse::InterfaceMacAddress::Bluetooth);
    let transport = BleConnector::new(peripherals.BT, Default::default()).unwrap();
    let ble = ble::run(ExternalController::<_, 1>::new(transport), mac.as_bytes().try_into().unwrap(), Some(store));
    let sleep_req = async {
        while !SLEEP_REQ.load(Relaxed) {
            Timer::after_millis(20).await;
        }
    };
    select(ble, sleep_req).await;
    // Recording is stopped and the flash writes are done (the BLE task waited for it); park the IMU, then sleep
    info!("sleep requested");
    // The store clears RECORDING before its final flush; its lock is held until that is done
    drop(store.lock().await);
    if has(bits::IMU_OK) {
        let _ = with_timeout(Duration::from_secs(1), IMU_ASLEEP.wait()).await;
    }
    Timer::after_millis(300).await;
    sleep::enter(peripherals.LPWR)
}

/// Timer wake while sleeping: no flash, no IMU, no LED. Advertise briefly, then back to deep sleep (or reset on 0x09).
async fn sleeping_cycle(bt: esp_hal::peripherals::BT<'static>, lpwr: esp_hal::peripherals::LPWR<'static>) -> ! {
    let t0 = Instant::now();
    STATE_EXT.store(oso_proto::state_ext::SLEEPING, Relaxed);
    let mac = esp_hal::efuse::interface_mac_address(esp_hal::efuse::InterfaceMacAddress::Bluetooth);
    let transport = BleConnector::new(bt, Default::default()).unwrap();
    let wake = ble::run(ExternalController::<_, 1>::new(transport), mac.as_bytes().try_into().unwrap(), None).await;
    if wake {
        info!("sleeping cycle: 0x09, waking up (awake {} ms)", t0.elapsed().as_millis());
        sleep::wake();
    }
    info!("sleeping cycle: awake {} ms, deep sleep for {} s", t0.elapsed().as_millis(), sleep::SLEEP_INTERVAL_S);
    // Let the USB serial FIFO drain so the line above is readable
    Timer::after_millis(30).await;
    sleep::enter(lpwr)
}
