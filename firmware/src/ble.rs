//! BLE peripheral (docs/ble-profile.md): `imu_stream` notify, `control` write, `status` read + notify, Battery Service.

// The gatt macros expand to borrows clippy dislikes
#![allow(clippy::needless_borrows_for_generic_args)]

use crate::shared::{
    BATTERY, CMD, CONNECTED, Cmd, EVENTS, LAST_ACT_MS, NOTIFY_ERRS, OVERFLOWS, SLEEP_REQ, STATE, STATE_EXT, STORED, STREAM,
    FRAMING_RESETS, STREAM_DROPS, UPLOAD_OK, UPLOAD_REQ, has, now_ms, now_us, set,
};
use crate::sleep::{SLEEP_ADV_MS, SLEEP_CONNECT_S};
use crate::store::Store;
use bt_hci::controller::ExternalController;
use core::fmt::Write;
use core::sync::atomic::Ordering::Relaxed;
use embassy_futures::select::{Either, Either3, select, select3, select4};
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex as Cs;
use embassy_sync::mutex::Mutex;
use embassy_time::{Duration, Instant, Ticker, Timer};
use esp_radio::ble::controller::BleConnector;
use heapless::Vec;
use oso_proto::{Marker, PACKET_LEN, SRC_CMD, Status, err, event, op, state as bits, uuid};
use trouble_host::prelude::*;

pub type Ctrl = ExternalController<BleConnector<'static>, 1>;

const CONNECTIONS_MAX: usize = 1;
const L2CAP_CHANNELS_MAX: usize = 2;
/// 13 packets at MTU 247
const MAX_NOTIFY: usize = 13;
const FW: (u8, u8) = (0, 1);

#[gatt_server]
struct Server {
    oso: OsoService,
    battery: BatteryService,
}

#[gatt_service(uuid = uuid::SERVICE)]
struct OsoService {
    #[characteristic(uuid = uuid::IMU_STREAM, notify)]
    imu_stream: Vec<u8, { MAX_NOTIFY * PACKET_LEN }>,
    #[characteristic(uuid = uuid::CONTROL, write, write_without_response)]
    control: Vec<u8, 8>,
    #[characteristic(uuid = uuid::STATUS, read, notify)]
    status: [u8; oso_proto::STATUS_LEN],
}

#[gatt_service(uuid = service::BATTERY)]
struct BatteryService {
    #[characteristic(uuid = characteristic::BATTERY_LEVEL, read, notify, value = 255)]
    level: u8,
}

fn status(ev: u8, value: u32) -> [u8; oso_proto::STATUS_LEN] {
    Status {
        fw_major: FW.0,
        fw_minor: FW.1,
        rate_hz: oso_proto::RATE_HZ,
        battery_pct: BATTERY.load(Relaxed),
        state_bits: STATE.load(Relaxed),
        accel_g: 16,
        gyro_dps: 2000,
        buffered_packets: STORED.load(Relaxed),
        fifo_overflows: OVERFLOWS.load(Relaxed),
        event: ev,
        event_value: value,
        state_ext: STATE_EXT.load(Relaxed),
        stream_drops: STREAM_DROPS.load(Relaxed),
        framing_resets: FRAMING_RESETS.load(Relaxed).min(255) as u8,
    }
    .encode()
}

/// Normal mode (`store` = Some) never returns. Sleeping cycle (`store` = None): advertises for `SLEEP_ADV_MS`, then
/// returns false (go back to deep sleep), or true when the host sent 0x09 (wake).
pub async fn run(controller: Ctrl, mac: [u8; 6], store: Option<&'static Mutex<Cs, Store>>) -> bool {
    let mut name = heapless::String::<12>::new();
    let _ = write!(name, "OSO-{:02X}{:02X}", mac[4], mac[5]);
    log::info!("BLE name {name}");

    let mut resources: HostResources<DefaultPacketPool, CONNECTIONS_MAX, L2CAP_CHANNELS_MAX> = HostResources::new();
    // Static random address derived from the MAC (top two bits set)
    let mut addr = mac;
    addr[5] |= 0xC0;
    let stack = trouble_host::new(controller, &mut resources).set_random_address(Address::random(addr));
    let Host { mut peripheral, mut runner, .. } = stack.build();
    let server = Server::new_with_config(GapConfig::Peripheral(PeripheralConfig {
        name: &name,
        appearance: &appearance::sensor::GENERIC_SENSOR,
    }))
    .unwrap();

    let mut adv = [0u8; 31];
    let adv_len = AdStructure::encode_slice(
        &[
            AdStructure::Flags(LE_GENERAL_DISCOVERABLE | BR_EDR_NOT_SUPPORTED),
            AdStructure::ServiceUuids128(&[uuid::SERVICE.to_le_bytes()]),
        ],
        &mut adv,
    )
    .unwrap();
    let mut scan = [0u8; 31];
    let scan_len = AdStructure::encode_slice(&[AdStructure::CompleteLocalName(name.as_bytes())], &mut scan).unwrap();

    // Sleeping: a short window, so advertise often (40 ms) for a central that scans at a low duty cycle to catch it
    let adv_params = AdvertisementParameters {
        interval_min: Duration::from_millis(if store.is_some() { 160 } else { 40 }),
        interval_max: Duration::from_millis(if store.is_some() { 160 } else { 40 }),
        ..Default::default()
    };
    let app = async {
        loop {
            log::info!("advertising");
            let acceptor = match peripheral
                .advertise(
                    &adv_params,
                    Advertisement::ConnectableScannableUndirected { adv_data: &adv[..adv_len], scan_data: &scan[..scan_len] },
                )
                .await
            {
                Ok(a) => a,
                Err(e) => {
                    log::error!("advertise: {e:?}");
                    Timer::after_millis(500).await;
                    continue;
                }
            };
            let accepted = if store.is_some() {
                acceptor.accept().await
            } else {
                match select(acceptor.accept(), Timer::after_millis(SLEEP_ADV_MS)).await {
                    Either::First(c) => c,
                    Either::Second(()) => return false,
                }
            };
            let Ok(conn) = accepted else { continue };
            let Ok(conn) = conn.with_attribute_server(&server) else { continue };
            log::info!(
                "connected peer {:?} mtu {} interval {} us",
                conn.raw().peer_address(),
                conn.raw().att_mtu(),
                conn.raw().params().conn_interval.as_micros()
            );
            let Some(store) = store else { return sleep_session(&server, &conn).await };
            EVENTS.clear();
            STREAM_DROPS.store(0, Relaxed);
            LAST_ACT_MS.store(now_ms(), Relaxed);
            CONNECTED.store(true, Relaxed);
            let params = RequestedConnParams {
                min_connection_interval: Duration::from_micros(7500),
                max_connection_interval: Duration::from_millis(15),
                max_latency: 0,
                min_event_length: Duration::from_secs(0),
                max_event_length: Duration::from_secs(0),
                supervision_timeout: Duration::from_secs(4),
            };
            let _ = server.set(&server.oso.status, &status(event::NONE, 0));
            // Concurrent with the GATT loop: awaited first, these hung when the host dropped the link during them, and
            // the pod never advertised again (the "stops advertising after a disconnect" quirk)
            let setup = async {
                if let Err(e) = conn.raw().update_connection_params(&stack, &params).await {
                    log::warn!("conn params: {e:?}");
                }
                if let Err(e) = conn.raw().update_data_length(&stack, 251, 2120).await {
                    log::warn!("data length: {e:?}");
                }
                log::info!(
                    "link mtu {} interval {} us",
                    conn.raw().att_mtu(),
                    conn.raw().params().conn_interval.as_micros()
                );
                core::future::pending::<()>().await
            };
            select4(events(&server, &conn), tx(&server, &conn, store), setup, idle_watchdog(&conn)).await;
            CONNECTED.store(false, Relaxed);
            log::info!("session ended");
            set(bits::STREAMING, false);
            set(bits::UPLOADING, false);
            UPLOAD_REQ.store(0, Relaxed);
        }
    };
    match select3(runner.run(), app, heartbeat()).await {
        Either3::First(r) => {
            log::error!("BLE runner stopped: {r:?}");
            loop {
                Timer::after_secs(1).await;
            }
        }
        Either3::Second(wake) => wake,
        Either3::Third(()) => unreachable!(),
    }
}

/// A host connected during a sleeping wake: it has `SLEEP_CONNECT_S` to send 0x09. True = wake up.
async fn sleep_session(server: &Server<'_>, conn: &Conn<'_, '_>) -> bool {
    let _ = server.set(&server.oso.status, &status(event::NONE, 0));
    let deadline = Instant::now() + Duration::from_secs(SLEEP_CONNECT_S);
    loop {
        let Either::First(ev) = select(conn.next(), Timer::at(deadline)).await else { return false };
        match ev {
            GattConnectionEvent::Disconnected { .. } => return false,
            GattConnectionEvent::Gatt { event: GattEvent::Write(w) } if w.handle() == server.oso.control.handle => {
                let opcode = w.data().first().copied();
                if let Ok(r) = w.accept() {
                    r.send().await;
                }
                if opcode == Some(op::WAKE) {
                    // Let the write response leave before the reset
                    Timer::after_millis(200).await;
                    return true;
                }
                let _ = send_status(server, conn, event::ERROR, err::SLEEPING).await;
                Timer::after_millis(300).await;
                return false;
            }
            GattConnectionEvent::Gatt { event } => {
                if let Ok(r) = event.accept() {
                    r.send().await;
                }
            }
            _ => {}
        }
    }
}

async fn events(server: &Server<'_>, conn: &GattConnection<'_, '_, DefaultPacketPool>) {
    loop {
        match conn.next().await {
            GattConnectionEvent::Disconnected { reason } => {
                log::info!("disconnected, reason {reason:?}");
                return;
            }
            GattConnectionEvent::ConnectionParamsUpdated { conn_interval, peripheral_latency, supervision_timeout } => {
                log::info!(
                    "conn params: interval {} us latency {peripheral_latency} timeout {} ms",
                    conn_interval.as_micros(),
                    supervision_timeout.as_millis()
                );
            }
            GattConnectionEvent::DataLengthUpdated { max_tx_octets, max_tx_time, .. } => {
                log::info!("data length: tx {max_tx_octets} B {max_tx_time} us");
            }
            GattConnectionEvent::Gatt { event: GattEvent::Write(w) } if w.handle() == server.oso.control.handle => {
                LAST_ACT_MS.store(now_ms(), Relaxed);
                let opcode = w.data().first().copied();
                if let Ok(r) = w.accept() {
                    r.send().await;
                }
                match opcode {
                    Some(o) => control(o).await,
                    None => error(err::BAD_OPCODE),
                }
            }
            GattConnectionEvent::Gatt { event } => {
                if let Ok(r) = event.accept() {
                    r.send().await;
                }
            }
            _ => {}
        }
    }
}

fn error(code: u32) {
    let _ = EVENTS.try_send((event::ERROR, code));
}

async fn control(opcode: u8) {
    match opcode {
        op::START_STREAM => {
            STREAM.clear();
            set(bits::STREAMING, true);
        }
        op::STOP_STREAM => set(bits::STREAMING, false),
        op::START_REC => {
            let _ = CMD.try_send(Cmd::StartRec);
        }
        op::STOP_REC => {
            let _ = CMD.try_send(Cmd::StopRec);
        }
        op::UPLOAD => {
            if has(bits::ERASING) || UPLOAD_REQ.load(Relaxed) != 0 {
                return error(err::BUSY);
            }
            // The session must be complete: stop a running recording first
            let _ = CMD.try_send(Cmd::StopRec);
            for _ in 0..100 {
                if !has(bits::RECORDING) {
                    break;
                }
                Timer::after_millis(10).await;
            }
            if has(bits::RECORDING) {
                return error(err::BUSY);
            }
            set(bits::STREAMING, false);
            UPLOAD_REQ.store(1, Relaxed);
        }
        op::SLEEP => {
            if has(bits::ERASING) || UPLOAD_REQ.load(Relaxed) != 0 {
                return error(err::BUSY);
            }
            // Same as 0x04 first: the session is marked complete before the pod sleeps
            let _ = CMD.try_send(Cmd::StopRec);
            for _ in 0..100 {
                if !has(bits::RECORDING) {
                    break;
                }
                Timer::after_millis(10).await;
            }
            if has(bits::RECORDING) {
                return error(err::BUSY);
            }
            set(bits::STREAMING, false);
            SLEEP_REQ.store(true, Relaxed);
        }
        op::MARK => {
            let _ = CMD.try_send(Cmd::Marker(Marker { t_us: now_us(), source: SRC_CMD }));
        }
        op::MARK_UPLOADED => {
            let _ = CMD.try_send(Cmd::MarkUploaded);
        }
        _ => error(err::BAD_OPCODE),
    }
}

type Conn<'a, 'b> = GattConnection<'a, 'b, DefaultPacketPool>;


/// Notify errors are usually transient (controller queue full, a weak link): one bad notify must not end the session
const NOTIFY_GIVE_UP: Duration = Duration::from_secs(2);
const NOTIFY_BACKOFF_MS: u64 = 30;
/// No host write and no streamed data for this long: the connection is forgotten, free it
const IDLE_LIMIT_MS: u32 = 30_000;

/// Tracks a run of consecutive notify errors
struct Notify {
    bad_since: Option<Instant>,
}

impl Notify {
    fn ok(&mut self) {
        self.bad_since = None;
    }

    /// Logs (at most 1/s, with the count), backs off, and says whether the errors have lasted too long
    async fn failed(&mut self, e: &Error) -> bool {
        static LAST_LOG_MS: core::sync::atomic::AtomicU32 = core::sync::atomic::AtomicU32::new(0);
        let n = NOTIFY_ERRS.fetch_add(1, Relaxed) + 1;
        let now = now_ms();
        if n == 1 || now.wrapping_sub(LAST_LOG_MS.load(Relaxed)) >= 1000 {
            LAST_LOG_MS.store(now, Relaxed);
            log::warn!("notify error {e:?} (#{n} since boot)");
        }
        let since = *self.bad_since.get_or_insert_with(Instant::now);
        Timer::after_millis(NOTIFY_BACKOFF_MS).await;
        since.elapsed() >= NOTIFY_GIVE_UP
    }
}

/// Asks the stack to close the link and waits for `events` to see it; the caller returns after 3 s regardless
async fn drop_link(conn: &Conn<'_, '_>, why: &str) {
    log::warn!("closing link: {why}");
    conn.raw().disconnect();
    Timer::after_secs(3).await;
}

async fn idle_watchdog(conn: &Conn<'_, '_>) {
    loop {
        Timer::after_secs(1).await;
        if now_ms().wrapping_sub(LAST_ACT_MS.load(Relaxed)) >= IDLE_LIMIT_MS {
            return drop_link(conn, "idle 30 s").await;
        }
    }
}

/// Every 10 s: link state and counters, in advertising and connected alike
async fn heartbeat() {
    loop {
        Timer::after_secs(10).await;
        log::info!(
            "hb {} streaming {} recording {} queue {} drops {} notify_errs {}",
            if CONNECTED.load(Relaxed) { "conn" } else { "adv" },
            has(bits::STREAMING),
            has(bits::RECORDING),
            STREAM.len(),
            STREAM_DROPS.load(Relaxed),
            NOTIFY_ERRS.load(Relaxed)
        );
    }
}

async fn send_status(server: &Server<'_>, conn: &Conn<'_, '_>, ev: u8, value: u32) -> Result<(), Error> {
    let s = status(ev, value);
    let _ = server.set(&server.oso.status, &s);
    let _ = server.set(&server.battery.level, &BATTERY.load(Relaxed));
    server.oso.status.notify(conn, &s).await
}

async fn tx(server: &Server<'_>, conn: &Conn<'_, '_>, store: &'static Mutex<Cs, Store>) {
    let mut tick = Ticker::every(Duration::from_millis(20));
    let mut last_status = Instant::now();
    let mut link = Notify { bad_since: None };
    loop {
        tick.next().await;
        // Keep the readable value fresh
        let _ = server.set(&server.oso.status, &status(event::NONE, 0));
        let n = (conn.raw().att_mtu() as usize).saturating_sub(3) / PACKET_LEN;
        let n = n.clamp(1, MAX_NOTIFY);
        if UPLOAD_REQ.load(Relaxed) == 1 {
            if !upload(server, conn, store, n).await {
                return drop_link(conn, "upload failed").await;
            }
            continue;
        }
        if has(bits::STREAMING) {
            while STREAM.len() >= n {
                let mut buf = Vec::<u8, { MAX_NOTIFY * PACKET_LEN }>::new();
                for _ in 0..n {
                    if let Ok(p) = STREAM.try_receive() {
                        let _ = buf.extend_from_slice(&p.encode());
                    }
                }
                match server.oso.imu_stream.notify(conn, &buf).await {
                    Ok(()) => {
                        link.ok();
                        LAST_ACT_MS.store(now_ms(), Relaxed);
                    }
                    Err(e) => {
                        // This batch is lost; the host sees the sequence gap
                        STREAM_DROPS.fetch_add(n as u16, Relaxed);
                        if link.failed(&e).await {
                            return drop_link(conn, "notify failing for 2 s").await;
                        }
                        break;
                    }
                }
            }
        }
        let sent = if let Ok((ev, v)) = EVENTS.try_receive() {
            last_status = Instant::now();
            Some(send_status(server, conn, ev, v).await)
        } else if last_status.elapsed() >= Duration::from_secs(1) {
            last_status = Instant::now();
            Some(send_status(server, conn, event::NONE, 0).await)
        } else {
            None
        };
        match sent {
            Some(Ok(())) => link.ok(),
            Some(Err(e)) if link.failed(&e).await => return drop_link(conn, "notify failing for 2 s").await,
            _ => {}
        }
    }
}

/// Sends the stored packets on imu_stream, then an `upload_done` status event. False when the link died or notify kept
/// failing for 2 s (0x07 is then never honored).
async fn upload(server: &Server<'_>, conn: &Conn<'_, '_>, store: &'static Mutex<Cs, Store>, n: usize) -> bool {
    set(bits::UPLOADING, true);
    UPLOAD_OK.store(false, Relaxed);
    EVENTS.clear();
    let (total, markers, n_markers) = {
        let s = store.lock().await;
        (s.info.count, s.info.markers, s.info.n_markers)
    };
    log::info!("upload of {total} packets");
    let (mut sent, mut ok) = (0u32, true);
    let mut link = Notify { bad_since: None };
    let mut raw = [0u8; MAX_NOTIFY * PACKET_LEN];
    while sent < total {
        let k = (total - sent).min(n as u32) as usize;
        if !store.lock().await.read_packets(sent, k, &mut raw) {
            error(err::NO_SESSION);
            break;
        }
        let Ok(buf) = Vec::<u8, { MAX_NOTIFY * PACKET_LEN }>::from_slice(&raw[..k * PACKET_LEN]) else { break };
        // Retry the same chunk with backoff until it goes through or 2 s of errors pass
        loop {
            match server.oso.imu_stream.notify(conn, &buf).await {
                Ok(()) => {
                    link.ok();
                    LAST_ACT_MS.store(now_ms(), Relaxed);
                    break;
                }
                Err(e) => {
                    if link.failed(&e).await {
                        ok = false;
                        break;
                    }
                }
            }
        }
        if !ok {
            break;
        }
        sent += k as u32;
    }
    if ok && sent == total {
        // The stored sync markers follow the data as status events
        for m in &markers[..n_markers] {
            let ev = if m.source == oso_proto::SRC_TAP { event::MARKER_TAP } else { event::MARKER_CMD };
            ok &= send_retry(server, conn, ev, m.t_us).await;
        }
    }
    if ok && sent == total {
        log::info!("upload done");
        ok &= send_retry(server, conn, event::UPLOAD_DONE, total).await;
        UPLOAD_OK.store(ok, Relaxed);
    }
    set(bits::UPLOADING, false);
    UPLOAD_REQ.store(0, Relaxed);
    ok
}

/// `send_status` with the same 2 s retry as the data chunks
async fn send_retry(server: &Server<'_>, conn: &Conn<'_, '_>, ev: u8, value: u32) -> bool {
    let mut link = Notify { bad_since: None };
    loop {
        match send_status(server, conn, ev, value).await {
            Ok(()) => return true,
            Err(e) if link.failed(&e).await => return false,
            Err(_) => {}
        }
    }
}
