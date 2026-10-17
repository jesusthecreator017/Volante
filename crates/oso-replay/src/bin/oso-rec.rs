//! Racquet-pod recorder (BLE central): talks to the firmware in firmware/ (profile: docs/ble-profile.md).
//!   oso-rec scan | status | mark | record start [--force]|stop | sleep | wake | stream -o imu.bin [--secs N] | upload -o imu.bin
//! Writes imu.bin (docs/data-format.md) and, when the device reported sync markers, imu.markers.json beside it.

use anyhow::{Context, bail};
use btleplug::api::{Central, CharPropFlags, Characteristic, Manager as _, Peripheral as _, ScanFilter, WriteType};
use btleplug::platform::{Adapter, Manager, Peripheral};
use futures::StreamExt;
use oso_replay::imu::packet;
use oso_proto::uuid as gatt;
use oso_proto::{PACKET_LEN, Status, event, op, state, state_ext};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use uuid::Uuid;

const USAGE: &str = "usage: oso-rec scan | status | mark | record start [--force]|stop | sleep | wake | stream -o imu.bin [--secs N] | upload -o imu.bin";

fn u(v: u128) -> Uuid {
    Uuid::from_u128(v)
}

async fn adapter() -> anyhow::Result<Adapter> {
    let m = Manager::new().await.context("Bluetooth manager (macOS: allow Bluetooth for this terminal)")?;
    m.adapters().await?.into_iter().next().context("no Bluetooth adapter")
}

async fn name_of(p: &Peripheral) -> Option<String> {
    p.properties().await.ok().flatten().and_then(|pr| pr.local_name)
}

/// Scans until an `OSO-*` device shows up (or `secs` pass); all matches for `scan`
async fn find(a: &Adapter, secs: u64, all: bool) -> anyhow::Result<Vec<(Peripheral, String, Option<i16>)>> {
    a.start_scan(ScanFilter::default()).await?;
    let t0 = Instant::now();
    let mut found = Vec::new();
    while t0.elapsed() < Duration::from_secs(secs) {
        tokio::time::sleep(Duration::from_millis(300)).await;
        found.clear();
        for p in a.peripherals().await? {
            if let Some(n) = name_of(&p).await.filter(|n| n.starts_with("OSO-")) {
                let rssi = p.properties().await.ok().flatten().and_then(|pr| pr.rssi);
                found.push((p, n, rssi));
            }
        }
        if !found.is_empty() && !all {
            break;
        }
    }
    a.stop_scan().await.ok();
    Ok(found)
}

struct Dev {
    p: Peripheral,
    stream: Characteristic,
    control: Characteristic,
    status: Characteristic,
}

/// A sleeping pod only advertises for 2 s every 30 s, so the scan has to outlast one sleep interval
const SCAN_SECS: u64 = 40;

async fn connect() -> anyhow::Result<Dev> {
    let a = adapter().await?;
    let t0 = Instant::now();
    let p = loop {
        let left = SCAN_SECS.saturating_sub(t0.elapsed().as_secs());
        let (p, name, rssi) = find(&a, left, false).await?.into_iter().next().context("no OSO-* device found")?;
        eprintln!("connecting to {name} (rssi {rssi:?})");
        // A sleeping pod can vanish before the link is up; the connect then hangs until the next advert
        match tokio::time::timeout(Duration::from_secs(8), p.connect()).await {
            Ok(r) => break r.map(|()| p)?,
            Err(_) => {
                eprintln!("connect timed out, scanning again");
                p.disconnect().await.ok();
            }
        }
    };
    p.discover_services().await?;
    let chars = p.characteristics();
    let get = |id: u128| {
        chars.iter().find(|c| c.uuid == u(id)).cloned().with_context(|| format!("characteristic {} missing", u(id)))
    };
    let dev = Dev { stream: get(gatt::IMU_STREAM)?, control: get(gatt::CONTROL)?, status: get(gatt::STATUS)?, p };
    anyhow::ensure!(dev.status.properties.contains(CharPropFlags::READ), "status not readable");
    Ok(dev)
}

impl Dev {
    async fn cmd(&self, opcode: u8) -> anyhow::Result<()> {
        Ok(self.p.write(&self.control, &[opcode], WriteType::WithResponse).await?)
    }

    async fn status(&self) -> anyhow::Result<Status> {
        Status::decode(&self.p.read(&self.status).await?).context("short status")
    }
}

fn describe(s: &Status) -> String {
    let bits: Vec<&str> = [
        (state::STREAMING, "streaming"),
        (state::RECORDING, "recording"),
        (state::UPLOADING, "uploading"),
        (state::PENDING_UPLOAD, "pending_upload"),
        (state::FLASH_FULL, "flash_full"),
        (state::IMU_OK, "imu_ok"),
        (state::IMU_IS_6500, "imu_is_6500"),
        (state::ERASING, "erasing"),
    ]
    .iter()
    .filter(|(b, _)| s.state_bits & b != 0)
    .map(|x| x.1)
    .collect();
    let bits: Vec<&str> =
        bits.into_iter().chain((s.state_ext & state_ext::SLEEPING != 0).then_some("sleeping")).collect();
    let batt = if s.battery_pct == 255 { "unknown".to_string() } else { format!("{} %", s.battery_pct) };
    format!(
        "fw {}.{}  {} Hz  +-{} g  +-{} dps  battery {}  stored {} packets  fifo overflows {}  [{}]",
        s.fw_major, s.fw_minor, s.rate_hz, s.accel_g, s.gyro_dps, batt, s.buffered_packets, s.fifo_overflows,
        bits.join(" ")
    )
}

/// imu.bin: header + packets in arrival order
fn write_imu(path: &Path, raw: &[[u8; PACKET_LEN]]) -> anyhow::Result<()> {
    let mut bytes = packet::encode(oso_proto::RATE_HZ, &[]);
    bytes.extend(raw.iter().flatten());
    // fsync: the device is told "uploaded" right after, so the data must be on disk first
    let write = || -> std::io::Result<()> {
        use std::io::Write;
        let mut f = std::fs::File::create(path)?;
        f.write_all(&bytes)?;
        f.sync_all()
    };
    write().with_context(|| format!("writing {}", path.display()))
}

fn write_markers(imu: &Path, markers: &[(u32, &str)]) -> anyhow::Result<()> {
    if markers.is_empty() {
        return Ok(());
    }
    let list: Vec<_> = markers.iter().map(|(t, s)| serde_json::json!({"t_us": t, "source": s})).collect();
    let path = imu.with_extension("markers.json");
    std::fs::write(&path, serde_json::to_string_pretty(&serde_json::json!({ "markers": list }))?)?;
    eprintln!("wrote {} ({} markers)", path.display(), markers.len());
    Ok(())
}

fn marker_source(ev: u8) -> Option<&'static str> {
    match ev {
        event::MARKER_TAP => Some("tap"),
        event::MARKER_CMD => Some("command"),
        _ => None,
    }
}

fn split(value: &[u8], raw: &mut Vec<[u8; PACKET_LEN]>) {
    raw.extend_from_slice(value.as_chunks::<PACKET_LEN>().0);
}

fn file_bytes(raw: &[[u8; PACKET_LEN]]) -> Vec<u8> {
    let mut b = packet::encode(oso_proto::RATE_HZ, &[]);
    b.extend(raw.iter().flatten());
    b
}

async fn stream(out: PathBuf, secs: Option<u64>) -> anyhow::Result<()> {
    let d = connect().await?;
    eprintln!("{}", describe(&d.status().await?));
    d.p.subscribe(&d.stream).await?;
    d.p.subscribe(&d.status).await?;
    let mut notes = d.p.notifications().await?;
    d.cmd(op::START_STREAM).await?;
    let (mut raw, mut markers): (Vec<[u8; PACKET_LEN]>, Vec<(u32, &str)>) = (Vec::new(), Vec::new());
    let (t0, mut tick, mut last_n) = (Instant::now(), tokio::time::interval(Duration::from_secs(1)), 0usize);
    tick.tick().await;
    loop {
        tokio::select! {
            n = notes.next() => {
                let Some(n) = n else { bail!("connection closed") };
                if n.uuid == u(gatt::IMU_STREAM) {
                    split(&n.value, &mut raw);
                } else if let Some(s) = Status::decode(&n.value)
                    && let Some(src) = marker_source(s.event)
                {
                    eprintln!("  marker ({src}) at device t_us={}", s.event_value);
                    markers.push((s.event_value, src));
                }
            }
            _ = tick.tick() => {
                // ponytail: re-parses the whole capture each second (fine for minutes; use a running counter for hours)
                let st = packet::parse(&file_bytes(&raw))?.stats;
                eprintln!(
                    "{:5.0}s  {:4} pkt/s  total {}  lost {} ({:.2} %)  dup {}  reordered {}",
                    t0.elapsed().as_secs_f32(), raw.len() - last_n, raw.len(), st.lost,
                    100.0 * st.lost as f64 / (st.unique as f64 + st.lost as f64).max(1.0), st.duplicate, st.reordered
                );
                last_n = raw.len();
                if secs.is_some_and(|s| t0.elapsed().as_secs() >= s) {
                    break;
                }
            }
            _ = tokio::signal::ctrl_c() => break,
        }
    }
    d.cmd(op::STOP_STREAM).await.ok();
    write_imu(&out, &raw)?;
    eprintln!("wrote {} ({} packets, {:.1} Hz average)", out.display(), raw.len(), raw.len() as f64 / t0.elapsed().as_secs_f64());
    write_markers(&out, &markers)?;
    d.p.disconnect().await.ok();
    Ok(())
}

async fn upload(out: PathBuf) -> anyhow::Result<()> {
    let d = connect().await?;
    let before = d.status().await?;
    eprintln!("{}", describe(&before));
    d.p.subscribe(&d.stream).await?;
    d.p.subscribe(&d.status).await?;
    let mut notes = d.p.notifications().await?;
    d.cmd(op::UPLOAD).await?;
    let (mut raw, mut markers): (Vec<[u8; PACKET_LEN]>, Vec<(u32, &str)>) = (Vec::new(), Vec::new());
    let t0 = Instant::now();
    let done = loop {
        let n = tokio::time::timeout(Duration::from_secs(20), notes.next())
            .await
            .context("upload stalled for 20 s")?
            .context("connection closed during upload")?;
        if n.uuid == u(gatt::IMU_STREAM) {
            split(&n.value, &mut raw);
            if raw.len() % 2000 < 13 {
                eprint!("\r  {} packets", raw.len());
            }
        } else if let Some(s) = Status::decode(&n.value) {
            if let Some(src) = marker_source(s.event) {
                markers.push((s.event_value, src));
            } else if s.event == event::UPLOAD_DONE {
                break s.event_value;
            } else if s.event == event::ERROR {
                bail!("device error {}", s.event_value);
            }
        }
    };
    eprintln!("\r  {} packets in {:.1} s", raw.len(), t0.elapsed().as_secs_f32());
    // The device's count is the header count: only a complete, written file earns the "uploaded" flag
    anyhow::ensure!(raw.len() as u32 == done, "received {} packets, the device says {done}: not marking uploaded", raw.len());
    write_imu(&out, &raw)?;
    write_markers(&out, &markers)?;
    let st = packet::parse(&std::fs::read(&out)?)?.stats;
    eprintln!("file parses: {} unique, {} lost (gaps from FIFO overflows), {} dup", st.unique, st.lost, st.duplicate);
    d.cmd(op::MARK_UPLOADED).await?;
    eprintln!("marked uploaded: the next power-on records again");
    d.p.disconnect().await.ok();
    Ok(())
}

/// `twice`: send the opcode again after 0.5 s (confirms a record-over-pending-session)
async fn simple(opcode: u8, twice: bool) -> anyhow::Result<()> {
    let d = connect().await?;
    d.p.subscribe(&d.status).await?;
    let mut notes = d.p.notifications().await?;
    d.cmd(opcode).await?;
    if twice {
        tokio::time::sleep(Duration::from_millis(500)).await;
        d.cmd(opcode).await?;
    }
    // Events come back as status notifications
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(2) {
        let Ok(Some(n)) = tokio::time::timeout(Duration::from_millis(500), notes.next()).await else { continue };
        if let Some(s) = Status::decode(&n.value)
            && s.event != event::NONE
        {
            match s.event {
                event::ERROR => eprintln!("device error {} (3 = un-uploaded session: upload it, or `record start --force` to overwrite; 5 = pod is sleeping: `oso-rec wake`)", s.event_value),
                e => eprintln!("event {e} value {}", s.event_value),
            }
        }
    }
    // A sleeping pod drops the link after answering, so this read may never complete
    match tokio::time::timeout(Duration::from_secs(3), d.status()).await {
        Ok(st) => eprintln!("{}", describe(&st?)),
        Err(_) => eprintln!("link closed by the device"),
    }
    tokio::time::timeout(Duration::from_secs(2), d.p.disconnect()).await.ok(); // hangs on a dead link
    Ok(())
}

/// 0x08: the pod stops recording, parks the IMU and deep-sleeps; the link then dies. Prints when it did.
async fn sleep() -> anyhow::Result<()> {
    let d = connect().await?;
    d.p.subscribe(&d.status).await?;
    let mut notes = d.p.notifications().await?;
    let before = d.status().await?;
    eprintln!("{}", describe(&before));
    anyhow::ensure!(before.state_ext & state_ext::SLEEPING == 0, "already sleeping");
    // The pod sleeps 300 ms after the write response, which can surface here as a disconnect
    if let Err(e) = d.cmd(op::SLEEP).await {
        eprintln!("sleep write: {e} (the link may have closed first)");
    }
    let t0 = Instant::now();
    // is_connected() can hang once the link is gone, hence the timeout (an expired one counts as disconnected)
    while tokio::time::timeout(Duration::from_secs(1), d.p.is_connected()).await.is_ok_and(|c| c.unwrap_or(false)) {
        anyhow::ensure!(t0.elapsed() < Duration::from_secs(15), "still connected 15 s after 0x08: busy? (error event below)");
        if let Ok(Some(n)) = tokio::time::timeout(Duration::from_millis(300), notes.next()).await
            && let Some(s) = Status::decode(&n.value)
            && s.event == event::ERROR
        {
            bail!("device error {} (4 = busy uploading/erasing)", s.event_value);
        }
    }
    eprintln!("pod asleep (link closed after {:.1} s); `oso-rec wake` brings it back within ~{} s", t0.elapsed().as_secs_f32(), SCAN_SECS - 10);
    Ok(())
}

/// Scans for the sleeping cycle's advert (up to SLEEP_INTERVAL_S + 10 s), sends 0x09, waits for the normal-mode status
async fn wake() -> anyhow::Result<()> {
    let d = connect().await?;
    let s = d.status().await?;
    eprintln!("{}", describe(&s));
    if s.state_ext & state_ext::SLEEPING == 0 {
        eprintln!("not sleeping");
        return Ok(());
    }
    // The pod resets right after answering, which can fail the write's completion on the host
    if let Err(e) = d.cmd(op::WAKE).await {
        eprintln!("wake write: {e} (the reset may have cut the link)");
    }
    drop(d);
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(45) {
        // The normal boot takes ~6 s (USB re-enumeration wait, IMU init)
        tokio::time::sleep(Duration::from_secs(3)).await;
        let Ok(Ok(d)) = tokio::time::timeout(Duration::from_secs(15), connect()).await else { continue };
        let Ok(s) = d.status().await else { continue };
        if s.state_ext & state_ext::SLEEPING == 0 {
            println!("{}", describe(&s));
            eprintln!("awake after {:.1} s", t0.elapsed().as_secs_f32());
            d.p.disconnect().await.ok();
            return Ok(());
        }
    }
    bail!("no normal-mode status within 45 s of 0x09")
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let flag = |f: &str| args.iter().position(|a| a == f).and_then(|i| args.get(i + 1));
    let out = || flag("-o").map(PathBuf::from).context(USAGE);
    match args.first().map(String::as_str) {
        Some("scan") => {
            let a = adapter().await?;
            for (_, n, rssi) in find(&a, 6, true).await? {
                println!("{n}  rssi {rssi:?}");
            }
            Ok(())
        }
        Some("status") => {
            let d = connect().await?;
            println!("{}", describe(&d.status().await?));
            Ok(())
        }
        Some("mark") => simple(op::MARK, false).await,
        Some("record") => match args.get(1).map(String::as_str) {
            Some("start") => simple(op::START_REC, args.iter().any(|a| a == "--force")).await,
            Some("stop") => simple(op::STOP_REC, false).await,
            _ => bail!(USAGE),
        },
        Some("sleep") => sleep().await,
        Some("wake") => wake().await,
        Some("stream") => stream(out()?, flag("--secs").map(|s| s.parse()).transpose()?).await,
        Some("upload") => upload(out()?).await,
        _ => bail!(USAGE),
    }
}
