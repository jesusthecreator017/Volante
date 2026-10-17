//! Drains the MPU FIFO every 10 ms into packet v1 and fans the packets out to the tap detector, the BLE stream and
//! the flash recorder.
//!
//! Sample times come from the MPU clock (the FIFO), reconstructed per batch: the newest sample gets the time the
//! count was read and earlier ones `k * period` before it, where `period` is a slow average of the measured MPU
//! sample period (the MPU clock is up to 1 % off the ESP clock, which the drift fit in `src/imu/sync.rs` needs).

use crate::imu::{FIFO_FULL, Imu, SAMPLE_BYTES, parse};
use crate::shared::{CMD, Cmd, IMU_ASLEEP, OVERFLOWS, REC, SLEEP_REQ, STREAM, has, now_us, push_lossy};
use core::sync::atomic::Ordering::Relaxed;
use embassy_time::{Duration, Ticker};
use oso_proto::{Marker, Packet, SRC_TAP, TapDetector, state as bits};

const MAX_BATCH: usize = FIFO_FULL as usize / SAMPLE_BYTES;
const NOMINAL_PERIOD_US: u32 = 2000;

#[embassy_executor::task]
pub async fn run(mut imu: Imu<'static>) {
    let mut buf = [0u8; MAX_BATCH * SAMPLE_BYTES];
    let mut det = TapDetector::new();
    let mut seq: u16 = 0;
    // Sample period in 1/256 us
    let mut period_q8 = NOMINAL_PERIOD_US << 8;
    let mut period_known = false;
    let mut last_read: Option<u32> = None;
    let (mut acc_dt, mut acc_n) = (0u32, 0u32);
    // Rate report window
    let (mut rep_t, mut rep_n) = (now_us(), 0u32);
    let mut last_v = [0i16; 6];
    let mut tick = Ticker::every(Duration::from_millis(10));
    loop {
        tick.next().await;
        if SLEEP_REQ.load(Relaxed) {
            if let Err(e) = imu.sleep().await {
                log::error!("imu sleep: {e:?}");
            }
            IMU_ASLEEP.signal(());
            return;
        }
        let t_read = now_us();
        let count = match imu.fifo_count().await {
            Ok(c) => c,
            Err(e) => {
                log::warn!("imu i2c: {e:?}");
                continue;
            }
        };
        if count >= FIFO_FULL {
            // The oldest bytes were overwritten, so the 12 B framing is gone: drop it all and leave a real gap in seq
            if imu.reset_fifo().await.is_err() {
                continue;
            }
            let n = OVERFLOWS.fetch_add(1, core::sync::atomic::Ordering::Relaxed) + 1;
            if let Some(last) = last_read {
                let lost = (t_read.wrapping_sub(last) as u64) << 8;
                seq = seq.wrapping_add((lost / period_q8 as u64) as u16);
            }
            last_read = Some(t_read);
            (acc_dt, acc_n) = (0, 0);
            log::warn!("imu FIFO overflow #{n}");
            continue;
        }
        let n = count as usize / SAMPLE_BYTES;
        if n == 0 {
            continue;
        }
        if imu.read_fifo(&mut buf[..n * SAMPLE_BYTES]).await.is_err() {
            // A burst cut short leaves the 12 B framing unknown: start over (the gap shows as a seq jump)
            let _ = imu.reset_fifo().await;
            if let Some(last) = last_read {
                let lost = (t_read.wrapping_sub(last) as u64) << 8;
                seq = seq.wrapping_add((lost / period_q8 as u64) as u16);
            }
            last_read = Some(t_read);
            (acc_dt, acc_n) = (0, 0);
            continue;
        }

        // Period estimate over >= 1 s windows
        if let Some(last) = last_read {
            acc_dt += t_read.wrapping_sub(last);
            acc_n += n as u32;
            if acc_dt >= 1_000_000 {
                let p = ((acc_dt as u64) << 8) / acc_n as u64;
                period_q8 = if period_known { (period_q8 as u64 * 3 + p) as u32 / 4 } else { p as u32 };
                period_known = true;
                (acc_dt, acc_n) = (0, 0);
            }
        }
        last_read = Some(t_read);

        for (i, s) in buf[..n * SAMPLE_BYTES].chunks_exact(SAMPLE_BYTES).enumerate() {
            let age = (n - 1 - i) as u64;
            let t_us = t_read.wrapping_sub(((age * period_q8 as u64) >> 8) as u32);
            let p = Packet { seq, t_us, v: parse(s) };
            seq = seq.wrapping_add(1);
            last_v = p.v;
            emit(&mut det, p);
        }

        rep_n += n as u32;
        let dt = t_read.wrapping_sub(rep_t);
        if dt >= 5_000_000 {
            log::info!(
                "imu {} Hz measured, period {} ns, overflows {}, last {:?}",
                (rep_n as u64 * 1_000_000 / dt as u64),
                (period_q8 as u64 * 1000) >> 8,
                OVERFLOWS.load(core::sync::atomic::Ordering::Relaxed),
                last_v
            );
            (rep_t, rep_n) = (t_read, 0);
        }
    }
}

/// Tap detector, BLE stream and flash recorder
fn emit(det: &mut TapDetector, p: Packet) {
    if let Some(t) = det.push(p) {
        log::info!("tap at t_us={t}");
        let _ = CMD.try_send(Cmd::Marker(Marker { t_us: t, source: SRC_TAP }));
    }
    if has(bits::STREAMING) {
        push_lossy(&STREAM, p);
    }
    if has(bits::RECORDING) && REC.try_send(p).is_err() {
        log::warn!("recorder ring full: packet {} not stored", p.seq);
    }
}

/// Bench stand-in for a missing IMU (feature `sim`): 500 Hz at rest, a tap every 10 s after 3 s of stillness.
#[cfg(feature = "sim")]
#[embassy_executor::task]
pub async fn sim() {
    let mut det = TapDetector::new();
    let mut tick = Ticker::every(Duration::from_millis(2));
    let mut seq: u16 = 0;
    loop {
        tick.next().await;
        let phase = seq as u32 % 5000;
        let tap = phase == 4000 || phase == 4001;
        let v = [(seq % 7) as i16 - 3, 0, 2048 + if tap { 12 * 2048 } else { 0 }, 3, -2, 1];
        emit(&mut det, Packet { seq, t_us: now_us(), v });
        seq = seq.wrapping_add(1);
    }
}
