//! Racquet-pod recorder (BLE central): talks to the firmware in firmware/ (profile: docs/ble-profile.md).
//!   oso-rec collect --player P07 [...] (guided stroke-dataset session, docs/strokes/collection.md) | scan | status | mark | record start [--force] [--no-view]|stop | live | sleep | wake | stream -o imu.bin [--secs N] | upload -o imu.bin
//! `record start` and `live` open the live racquet window (keys: R re-centre, M sync marker, Esc stop streaming).
//!   oso-rec profile SESSION_DIR|imu.bin ... -o my_style.json [--mount identity|flat|FILE]: the Volante Twins style profile (offline, no pod; docs/twins-app.md)
//! Writes imu.bin (docs/data-format.md) and, when the device reported sync markers, imu.markers.json beside it.

use anyhow::{Context, bail};
use btleplug::api::{Central, CharPropFlags, Characteristic, Manager as _, Peripheral as _, ScanFilter, WriteType};
use btleplug::platform::{Adapter, Manager, Peripheral};
use futures::StreamExt;
use futures::channel::mpsc::{UnboundedReceiver, UnboundedSender, unbounded};
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{Receiver, Sender, SyncSender, TrySendError, channel, sync_channel};
use std::sync::Arc;
use oso_render::live::{LiveFrame, LiveKey, LiveShot, LiveSource};
use oso_replay::collect::args::CollectArgs;
use oso_replay::collect::blocks::{Effect, Key, Runner, Status as BlockStatus, View, clock, headline};
use oso_replay::collect::parts;
use oso_replay::collect::protocol::{self, Protocol};
use oso_replay::collect::session::{self, MarkerRec, MountAxes, PartRec, Pod, Player, ProtocolRef, Session};
use oso_replay::imu::live::LiveFusion;
use oso_replay::imu::swingdet::{SwingCounter, THRESHOLD_DPS};
use oso_replay::imu::mount::{Calibration, Mount};
use oso_replay::imu::packet;
use oso_replay::imu::profile;
use oso_proto::uuid as gatt;
use oso_proto::{PACKET_LEN, Status, event, op, state, state_ext};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use uuid::Uuid;

const USAGE: &str = "usage: oso-rec scan | status | mark | record start [--force] [--no-view]|stop | calibrate | live [--mount identity|flat|FILE] [--secs N] [--replay-file imu.bin [--time S] [--screenshot out.png]] | sleep | wake | stream -o imu.bin [--secs N] | upload -o imu.bin | profile SESSION_DIR|imu.bin... -o my_style.json [--mount identity|flat|FILE] | collect --player P07 (see `oso-rec collect` flags in docs/strokes/collection.md)";

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
        "fw {}.{}  {} Hz  +-{} g  +-{} dps  battery {}  stored {} packets  fifo overflows {}  framing resets {}  [{}]",
        s.fw_major, s.fw_minor, s.rate_hz, s.accel_g, s.gyro_dps, batt, s.buffered_packets, s.fifo_overflows,
        s.framing_resets, bits.join(" ")
    )
}

/// Prints the misalignment warning when a written imu.bin shows still-but-wrong gravity
fn warn_if_misaligned(path: &Path) {
    if let Ok(c) = std::fs::read(path).map_err(anyhow::Error::from).and_then(|b| packet::parse(&b))
        && packet::implausible_rest(&c.samples)
    {
        eprintln!("WARNING: {}", packet::MISALIGNED_WARNING);
    }
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
    warn_if_misaligned(&out);
    eprintln!("wrote {} ({} packets, {:.1} Hz average)", out.display(), raw.len(), raw.len() as f64 / t0.elapsed().as_secs_f64());
    write_markers(&out, &markers)?;
    d.p.disconnect().await.ok();
    Ok(())
}

/// Downloads the pod's session into `out`; the markers the pod stored with it
async fn upload(out: PathBuf) -> anyhow::Result<Vec<(u32, &'static str)>> {
    upload_checked(out, &|_| Ok(())).await
}

/// Like `upload`, with `check` run on the written file before the pod is told "uploaded": a file that fails it is deleted and
/// the pod keeps its session
async fn upload_checked(out: PathBuf, check: &(dyn Fn(&packet::Capture) -> anyhow::Result<()> + Sync)) -> anyhow::Result<Vec<(u32, &'static str)>> {
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
    warn_if_misaligned(&out);
    let cap = packet::parse(&std::fs::read(&out)?)?;
    let st = cap.stats;
    eprintln!("file parses: {} unique, {} lost (gaps from FIFO overflows), {} dup", st.unique, st.lost, st.duplicate);
    if let Err(e) = check(&cap) {
        std::fs::remove_file(&out).ok();
        std::fs::remove_file(out.with_extension("markers.json")).ok();
        d.p.disconnect().await.ok();
        return Err(e.context("not marking uploaded: the pod keeps its session"));
    }
    d.cmd(op::MARK_UPLOADED).await?;
    eprintln!("marked uploaded: the next power-on records again");
    d.p.disconnect().await.ok();
    Ok(markers)
}

type Notes = Pin<Box<dyn futures::Stream<Item = btleplug::api::ValueNotification> + Send>>;

/// Sends `opcode` (`twice`: again after 0.5 s, confirms a record-over-pending-session) and prints the events that come
/// back as status notifications for 2 s. True when the device answered with an error event.
async fn run_cmd(d: &Dev, notes: &mut Notes, opcode: u8, twice: bool) -> anyhow::Result<bool> {
    d.cmd(opcode).await?;
    if twice {
        tokio::time::sleep(Duration::from_millis(500)).await;
        d.cmd(opcode).await?;
    }
    let mut failed = false;
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(2) {
        let Ok(Some(n)) = tokio::time::timeout(Duration::from_millis(500), notes.next()).await else { continue };
        if let Some(s) = Status::decode(&n.value)
            && s.event != event::NONE
        {
            match s.event {
                event::ERROR => {
                    failed = true;
                    eprintln!("device error {} (3 = un-uploaded session: upload it, or `record start --force` to overwrite; 5 = pod is sleeping: `oso-rec wake`)", s.event_value)
                }
                e => eprintln!("event {e} value {}", s.event_value),
            }
        }
    }
    Ok(failed)
}

async fn simple(opcode: u8, twice: bool) -> anyhow::Result<()> {
    let d = connect().await?;
    d.p.subscribe(&d.status).await?;
    let mut notes = d.p.notifications().await?;
    run_cmd(&d, &mut notes, opcode, twice).await?;
    // A sleeping pod drops the link after answering, so this read may never complete
    match tokio::time::timeout(Duration::from_secs(3), d.status()).await {
        Ok(st) => eprintln!("{}", describe(&st?)),
        Err(_) => eprintln!("link closed by the device"),
    }
    tokio::time::timeout(Duration::from_secs(2), d.p.disconnect()).await.ok(); // hangs on a dead link
    Ok(())
}

// ---- live racquet view -------------------------------------------------------------------------------------------

/// BLE thread -> window, rare control events (unbounded; the stream itself goes over a bounded channel)
enum Msg {
    /// `state_bits` of the latest status
    State(u8),
    /// The link dropped; the BLE thread is re-establishing it
    Reconnecting,
    Reconnected,
}

/// Window -> BLE thread
enum Cmd {
    Marker,
    Stop,
}

/// Stream batches (up to 13 packets each) the window may lag behind by: ~6 s at 500 Hz. Beyond it the newest batch is
/// dropped (counted), which the sequence gap then shows as loss.
const DATA_BATCHES: usize = 256;

/// No stream data for this long while streaming: the link is treated as lost (a hung link may never report it)
const STALL: Duration = Duration::from_secs(3);
/// Wait between reconnect attempts (each attempt itself scans up to `SCAN_SECS`)
const RETRY_WAIT: Duration = Duration::from_secs(5);

/// HH:MM:SS UTC, for the reconnect log
fn stamp() -> String {
    let s = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs()) % 86_400;
    format!("{:02}:{:02}:{:02}Z", s / 3600, s / 60 % 60, s % 60)
}

/// Runs `attempt` until it succeeds, logging each failure and waiting `wait` between tries
async fn retry<T, F: AsyncFnMut() -> anyhow::Result<T>>(mut attempt: F, wait: Duration) -> T {
    loop {
        match attempt().await {
            Ok(v) => return v,
            Err(e) => {
                eprintln!("[{}] reconnect failed: {e:#}; retrying in {} s", stamp(), wait.as_secs());
                tokio::time::sleep(wait).await;
            }
        }
    }
}

/// A fresh connection that is streaming (0x01) with both characteristics subscribed
async fn open() -> anyhow::Result<(Dev, Notes)> {
    let d = connect().await?;
    // A half-open link left connected would keep the pod from advertising, so close it on any failure
    let setup = async {
        d.p.subscribe(&d.stream).await?;
        d.p.subscribe(&d.status).await?;
        let notes: Notes = d.p.notifications().await?;
        d.cmd(op::START_STREAM).await?;
        anyhow::Ok(notes)
    };
    match setup.await {
        Ok(notes) => Ok((d, notes)),
        Err(e) => {
            tokio::time::timeout(Duration::from_secs(3), d.p.disconnect()).await.ok();
            Err(e)
        }
    }
}

enum End {
    Stop,
    Lost,
}

/// One connection's worth of forwarding: ends on a window Stop, or when the link closes or goes quiet
async fn session(
    d: &Dev,
    notes: &mut Notes,
    data: &SyncSender<Vec<[u8; PACKET_LEN]>>,
    ctl: &Sender<Msg>,
    dropped: &AtomicU64,
    cmds: &mut UnboundedReceiver<Cmd>,
) -> End {
    let (mut last_data, mut tick) = (Instant::now(), tokio::time::interval(Duration::from_secs(1)));
    loop {
        tokio::select! {
            n = notes.next() => match n {
                None => return End::Lost,
                Some(n) if n.uuid == u(gatt::IMU_STREAM) => {
                    last_data = Instant::now();
                    let batch = n.value.as_chunks::<PACKET_LEN>().0.to_vec();
                    if let Err(TrySendError::Full(b)) = data.try_send(batch) {
                        dropped.fetch_add(b.len() as u64, Ordering::Relaxed);
                    }
                }
                Some(n) => {
                    if let Some(s) = Status::decode(&n.value) {
                        ctl.send(Msg::State(s.state_bits)).ok();
                        if let Some(src) = marker_source(s.event) {
                            eprintln!("  marker ({src}) at device t_us={}", s.event_value);
                        }
                    }
                }
            },
            _ = tick.tick() => if last_data.elapsed() > STALL {
                eprintln!("[{}] no stream data for {} s", stamp(), STALL.as_secs());
                return End::Lost;
            },
            c = cmds.next() => match c {
                Some(Cmd::Marker) => {
                    // Hangs on a dead link
                    match tokio::time::timeout(Duration::from_secs(3), d.cmd(op::MARK)).await {
                        Ok(Err(e)) => eprintln!("marker: {e}"),
                        Err(_) => eprintln!("marker: timed out"),
                        Ok(Ok(())) => {}
                    }
                }
                Some(Cmd::Stop) | None => return End::Stop,
            },
        }
    }
}

/// Forwards the stream to the window and the window's keys to the pod. Ends on Stop (0x02 + disconnect). A lost link
/// shows "reconnecting" and is re-established (scan, connect, 0x01) until it works or the window closes; the window's
/// fusion, mount and re-centre are untouched.
async fn stream_task(
    d: Dev,
    notes: Notes,
    data: SyncSender<Vec<[u8; PACKET_LEN]>>,
    ctl: Sender<Msg>,
    dropped: Arc<AtomicU64>,
    mut cmds: UnboundedReceiver<Cmd>,
) {
    let (mut d, mut notes) = (d, notes);
    loop {
        if let End::Stop = session(&d, &mut notes, &data, &ctl, &dropped, &mut cmds).await {
            // Both hang on a dead link
            tokio::time::timeout(Duration::from_secs(3), d.cmd(op::STOP_STREAM)).await.ok();
            tokio::time::timeout(Duration::from_secs(3), d.p.disconnect()).await.ok();
            return;
        }
        eprintln!("[{}] link lost, reconnecting", stamp());
        ctl.send(Msg::Reconnecting).ok();
        // Marker presses while away are dropped; Stop (or the window closing) ends the task, even mid-disconnect
        let stopped = async {
            while let Some(Cmd::Marker) = cmds.next().await {}
        };
        let reopen = async {
            tokio::time::timeout(Duration::from_secs(3), d.p.disconnect()).await.ok();
            retry(open, RETRY_WAIT).await
        };
        tokio::select! {
            (nd, nn) = reopen => {
                eprintln!("[{}] reconnected", stamp());
                (d, notes) = (nd, nn);
                ctl.send(Msg::Reconnected).ok();
            }
            () = stopped => return,
        }
    }
}

/// How often the title text is rebuilt (it also changes at once on a key)
const TITLE_EVERY: Duration = Duration::from_millis(250);
/// How long a calibration message stays in the title
const NOTICE_FOR: Duration = Duration::from_secs(5);

/// The window's data source for a pod
struct PodView {
    data: Receiver<Vec<[u8; PACKET_LEN]>>,
    ctl: Receiver<Msg>,
    dropped: Arc<AtomicU64>,
    cmds: UnboundedSender<Cmd>,
    fusion: LiveFusion,
    mount: Mount,
    calib: Calibration,
    notice: Option<(String, Instant)>,
    state_bits: u8,
    connected: bool,
    hz: f64,
    /// Window start and packets received since
    window: (Instant, u64),
    /// `--secs`: close the window after this long
    deadline: Option<Instant>,
    title: String,
    title_at: Instant,
    dirty: bool,
    /// Last few seconds of samples for the misalignment check, and whether it was already reported
    recent: Vec<packet::Sample>,
    warned: bool,
}

impl PodView {
    fn new(
        data: Receiver<Vec<[u8; PACKET_LEN]>>,
        ctl: Receiver<Msg>,
        dropped: Arc<AtomicU64>,
        cmds: UnboundedSender<Cmd>,
        mount: Mount,
        secs: Option<f64>,
    ) -> Self {
        Self {
            data,
            ctl,
            dropped,
            cmds,
            fusion: LiveFusion::with_mount(mount),
            mount,
            calib: Calibration::Idle,
            notice: None,
            state_bits: state::STREAMING,
            connected: true,
            hz: 0.0,
            window: (Instant::now(), 0),
            deadline: secs.map(|s| Instant::now() + Duration::from_secs_f64(s)),
            title: String::new(),
            title_at: Instant::now(),
            dirty: true,
            recent: Vec::new(),
            warned: false,
        }
    }

    /// C: next calibration step with the last second of raw samples
    fn calibrate_key(&mut self) {
        let msg = match self.calib.press(&self.fusion.recent_raw()) {
            Ok(None) => None,
            Ok(Some(m)) => {
                self.mount = m;
                self.fusion = LiveFusion::with_mount(m);
                Some(match save_mount(&m) {
                    Ok(p) => format!("mount saved ({})", p.display()),
                    Err(e) => format!("mount applied, not saved: {e}"),
                })
            }
            Err(reason) => Some(format!("rejected: {reason}")),
        };
        self.notice = msg.map(|m| (m, Instant::now()));
        self.dirty = true;
    }
}

fn save_mount(m: &Mount) -> anyhow::Result<PathBuf> {
    let path = Mount::default_path().context("no HOME")?;
    m.save(&path)?;
    Ok(path)
}

impl LiveSource for PodView {
    fn frame(&mut self) -> LiveFrame {
        while let Ok(m) = self.ctl.try_recv() {
            match m {
                Msg::State(b) => self.state_bits = b,
                Msg::Reconnecting => self.connected = false,
                Msg::Reconnected => {
                    self.connected = true;
                    self.state_bits = state::STREAMING;
                }
            }
            self.dirty = true;
        }
        while let Ok(ps) = self.data.try_recv() {
            self.window.1 += ps.len() as u64;
            ps.iter().for_each(|p| self.fusion.push_packet(p));
            if !self.warned {
                for p in ps.iter().map(oso_proto::Packet::decode) {
                    self.recent.push(packet::to_sample(p.t_us as i64, p.v));
                }
            }
        }
        let el = self.window.0.elapsed().as_secs_f64();
        let rolled = el >= 1.0;
        if rolled {
            self.hz = self.window.1 as f64 / el;
            self.window = (Instant::now(), 0);
            if !self.warned {
                // ponytail: ignores the u32 t_us wrap (71 min); a wrap only delays the warning by one window
                let end = self.recent.last().map_or(0.0, |s| s.t_dev);
                self.recent.retain(|s| end - s.t_dev <= packet::IMPLAUSIBLE_WINDOW_S + 1.0);
                if packet::implausible_rest(&self.recent) {
                    self.warned = true;
                    self.notice = Some((packet::MISALIGNED_WARNING.to_string(), Instant::now()));
                    eprintln!("WARNING: {}", packet::MISALIGNED_WARNING);
                }
            }
        }
        if self.notice.as_ref().is_some_and(|(_, t)| t.elapsed() > NOTICE_FOR) {
            self.notice = None;
            self.dirty = true;
        }
        let mut new_title = None;
        if self.dirty || self.title_at.elapsed() >= TITLE_EVERY {
            let total = (self.fusion.received + self.fusion.lost).max(1) as f64;
            let t = title(&TitleInfo {
                connected: self.connected,
                state_bits: self.state_bits,
                calibrating: self.fusion.calibrating(),
                hz: self.hz,
                loss_pct: 100.0 * self.fusion.lost as f64 / total,
                dropped: self.dropped.load(Ordering::Relaxed),
                prompt: self.notice.as_ref().map_or_else(|| self.calib.prompt(), |(m, _)| m),
            });
            self.title_at = Instant::now();
            self.dirty = false;
            if t != self.title {
                if rolled {
                    eprintln!("{t}");
                }
                self.title = t.clone();
                new_title = Some(t);
            }
        }
        LiveFrame { rotation: self.fusion.orientation(), title: new_title, quit: self.deadline.is_some_and(|d| Instant::now() >= d) }
    }

    fn key(&mut self, key: LiveKey) {
        match key {
            LiveKey::Recenter => self.fusion.recenter(),
            LiveKey::Marker => {
                self.cmds.unbounded_send(Cmd::Marker).ok();
            }
            LiveKey::Calibrate => self.calibrate_key(),
            LiveKey::Quit => {
                self.cmds.unbounded_send(Cmd::Stop).ok();
            }
        }
    }
}

struct TitleInfo<'a> {
    connected: bool,
    state_bits: u8,
    calibrating: bool,
    hz: f64,
    loss_pct: f64,
    dropped: u64,
    /// Calibration prompt or message ("" = none)
    prompt: &'a str,
}

fn title(i: &TitleInfo) -> String {
    let mode = if !i.connected {
        "reconnecting…".to_string()
    } else {
        let mut v = Vec::new();
        if i.state_bits & state::RECORDING != 0 {
            v.push("recording");
        }
        v.push("streaming");
        if i.calibrating {
            v.push("calibrating: hold still");
        }
        v.join(" + ")
    };
    let dropped = if i.dropped > 0 { format!(" ({} dropped)", i.dropped) } else { String::new() };
    let prompt = if i.prompt.is_empty() { String::new() } else { format!("{} | ", i.prompt) };
    format!("{prompt}oso-rec live | {mode} | {:.0} Hz | loss {:.1} %{dropped} | R re-centre, M marker, C calibrate mount, Esc stop", i.hz, i.loss_pct)
}

/// A recorded imu.bin played back in real time (or frozen at `--time` for a screenshot): no BLE
struct FileView {
    samples: Vec<packet::Sample>,
    next: usize,
    start: Instant,
    /// Some: frozen at this many seconds into the file
    at: Option<f64>,
    fusion: LiveFusion,
    titled: bool,
}

impl LiveSource for FileView {
    fn frame(&mut self) -> LiveFrame {
        let t0 = self.samples.first().map_or(0.0, |s| s.t_dev);
        let now = self.at.unwrap_or_else(|| self.start.elapsed().as_secs_f64());
        while self.samples.get(self.next).is_some_and(|s| s.t_dev - t0 <= now) {
            self.fusion.push_sample(self.samples[self.next]);
            self.next += 1;
        }
        let title = (!std::mem::replace(&mut self.titled, true)).then(|| "oso-rec live (file) | R re-centre, Esc quit".to_string());
        LiveFrame { rotation: self.fusion.orientation(), title, quit: false }
    }

    fn key(&mut self, key: LiveKey) {
        if let LiveKey::Recenter = key {
            self.fusion.recenter();
        }
    }
}

/// `record start` flags: (force, view)
fn record_start_flags(args: &[String]) -> (bool, bool) {
    (args.iter().any(|a| a == "--force"), !args.iter().any(|a| a == "--no-view"))
}

/// Starts the recording (`record`: Some(force)), then streams into the window. Esc leaves the recording running.
async fn live(record: Option<bool>, secs: Option<f64>, mount: Mount) -> anyhow::Result<()> {
    let d = connect().await?;
    eprintln!("{}", describe(&d.status().await?));
    d.p.subscribe(&d.stream).await?;
    d.p.subscribe(&d.status).await?;
    let mut notes: Notes = d.p.notifications().await?;
    if let Some(force) = record {
        if run_cmd(&d, &mut notes, op::START_REC, force).await? {
            d.p.disconnect().await.ok();
            bail!("recording not started");
        }
        eprintln!("recording started");
    }
    d.cmd(op::START_STREAM).await?;
    let (data_tx, data_rx) = sync_channel(DATA_BATCHES);
    let (tx, rx) = channel();
    let dropped = Arc::new(AtomicU64::new(0));
    let bits = d.status().await?.state_bits;
    tx.send(Msg::State(bits)).ok();
    let (ctx, crx) = unbounded();
    let stop = ctx.clone();
    let task = tokio::spawn(stream_task(d, notes, data_tx, tx, dropped.clone(), crx));
    // The window runs on this (the main) thread, as winit requires; the BLE work is on the runtime's workers
    let shown = oso_render::live::run_live(Box::new(PodView::new(data_rx, rx, dropped, ctx, mount, secs)), None);
    // A window that failed to open must not leave the pod streaming
    stop.unbounded_send(Cmd::Stop).ok();
    anyhow::ensure!(tokio::time::timeout(Duration::from_secs(10), task).await.is_ok(), "pod did not disconnect in time");
    shown?;
    if record.is_some() || bits & state::RECORDING != 0 {
        eprintln!("pod still recording, run `oso-rec record stop` to end");
    } else {
        eprintln!("streaming stopped");
    }
    Ok(())
}

/// `oso-rec calibrate`: the two-step mount calibration without the window (prompts in the terminal, Enter = go)
async fn calibrate() -> anyhow::Result<()> {
    let d = connect().await?;
    d.p.subscribe(&d.stream).await?;
    let mut notes: Notes = d.p.notifications().await?;
    d.cmd(op::START_STREAM).await?;
    let result: anyhow::Result<Mount> = async {
        let mut fusion = LiveFusion::new();
        let mut cal = Calibration::Idle;
        cal.press(&[]).ok();
        loop {
            eprintln!("{}, then press Enter", cal.prompt());
            // Keep draining the stream while waiting, so the last second before Enter is what gets averaged
            let mut line = tokio::task::spawn_blocking(|| std::io::stdin().read_line(&mut String::new()));
            loop {
                tokio::select! {
                    _ = &mut line => break,
                    n = notes.next() => {
                        let n = n.context("connection closed")?;
                        n.value.as_chunks::<PACKET_LEN>().0.iter().for_each(|p| fusion.push_packet(p));
                    }
                }
            }
            match cal.press(&fusion.recent_raw()) {
                Ok(Some(m)) => return Ok(m),
                Ok(None) => {}
                Err(reason) => eprintln!("rejected: {reason}"),
            }
        }
    }
    .await;
    tokio::time::timeout(Duration::from_secs(3), d.cmd(op::STOP_STREAM)).await.ok();
    tokio::time::timeout(Duration::from_secs(3), d.p.disconnect()).await.ok();
    let m: Mount = result?;
    eprintln!("mount saved ({})", save_mount(&m)?.display());
    Ok(())
}

fn live_file(path: &Path, at: Option<f64>, shot: Option<PathBuf>, mount: Mount) -> anyhow::Result<()> {
    let samples = packet::parse(&std::fs::read(path)?)?.samples;
    let view = FileView { samples, next: 0, start: Instant::now(), at: shot.as_ref().map(|_| at.unwrap_or(5.0)).or(at), fusion: LiveFusion::with_mount(mount), titled: false };
    oso_render::live::run_live(Box::new(view), shot.map(|path| LiveShot { path, size: (900, 700) }))
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

// ---- guided stroke collection (Phase 16.1) -------------------------------------------------------------------------

/// The terminal in single-key mode (no Enter, no echo) while alive. `stty` rather than a termios crate: no new dependency.
/// Not a terminal (a pipe in a test): keys are read as they come.
struct RawTty(Option<String>);

impl RawTty {
    fn enter() -> Self {
        use std::io::IsTerminal;
        let stty = |a: &[&str]| std::process::Command::new("stty").args(a).stdin(std::process::Stdio::inherit()).output().ok();
        if !std::io::stdin().is_terminal() {
            return Self(None);
        }
        let saved = stty(&["-g"]).map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).filter(|s| !s.is_empty());
        if saved.is_some() {
            stty(&["-icanon", "-echo", "min", "1"]);
        }
        Self(saved)
    }
}

impl Drop for RawTty {
    fn drop(&mut self) {
        if let Some(s) = &self.0 {
            std::process::Command::new("stty").arg(s).stdin(std::process::Stdio::inherit()).output().ok();
        }
    }
}

/// Stdin bytes as they arrive (the thread ends with the process: a blocked `read` cannot be cancelled)
fn stdin_bytes() -> UnboundedReceiver<u8> {
    use std::io::Read;
    let (tx, rx) = unbounded();
    std::thread::spawn(move || {
        let mut b = [0u8; 1];
        while std::io::stdin().lock().read(&mut b).is_ok_and(|n| n == 1) {
            if tx.unbounded_send(b[0]).is_err() {
                break;
            }
        }
    });
    rx
}

/// One line from the terminal, blocking (before the key thread starts)
fn ask(q: &str) -> String {
    eprint!("{q}");
    let mut l = String::new();
    std::io::stdin().read_line(&mut l).ok();
    l.trim().to_string()
}

/// Between the window (main thread) and the collect task
struct Shared {
    title: std::sync::Mutex<String>,
    /// The task is over: close the window
    done: std::sync::atomic::AtomicBool,
}

/// The live window during `collect`: the racquet follows the stream, the title shows the block
struct CollectView {
    data: Receiver<Vec<[u8; PACKET_LEN]>>,
    fusion: LiveFusion,
    shared: Arc<Shared>,
    shown: String,
}

impl LiveSource for CollectView {
    fn frame(&mut self) -> LiveFrame {
        while let Ok(ps) = self.data.try_recv() {
            ps.iter().for_each(|p| self.fusion.push_packet(p));
        }
        let t = self.shared.title.lock().map(|t| t.clone()).unwrap_or_default();
        let title = (t != self.shown).then(|| t.clone());
        self.shown = t;
        LiveFrame { rotation: self.fusion.orientation(), title, quit: self.shared.done.load(Ordering::Relaxed) }
    }

    fn key(&mut self, key: LiveKey) {
        if let LiveKey::Recenter = key {
            self.fusion.recenter();
        }
    }
}

fn side_word(s: &str) -> &str {
    if s == "FH" { "FOREHAND" } else { "BACKHAND" }
}

/// The terminal screen: big block name, count, instruction, keys
fn screen(v: &View, player: &str, session_s: f64, link: &str) -> String {
    let mut o = format!("\x1b[H\x1b[J\x1b[1moso-rec collect\x1b[0m  {player}  session {}  {link}\n\n", clock(session_s));
    let Some(b) = v.block else { return o + "  All blocks done. Finishing...\n" };
    let what = b.side.as_deref().map_or(b.label.to_uppercase(), |s| format!("{} {}", side_word(s), b.label.to_uppercase()));
    o += &format!("  BLOCK {} / {}{}\n\n  \x1b[1;7m  {what}  \x1b[0m\n\n", v.idx + 1, v.total, if b.optional { "  (optional)" } else { "" });
    if v.active {
        match b.secs {
            Some(s) => o += &format!("  \x1b[1m{} / {}\x1b[0m   {} reps seen\n", clock(v.elapsed_s), clock(f64::from(s)), v.counted),
            None => {
                let filled = (v.counted.min(b.target) * 25 / b.target.max(1)) as usize;
                o += &format!("  \x1b[1m{} / {} reps\x1b[0m   {}   [{}{}]\n", v.counted, b.target, clock(v.elapsed_s), "#".repeat(filled), "-".repeat(25 - filled));
            }
        }
        if v.done {
            o += "\n  \x1b[1;32mDONE: press SPACE for the next block\x1b[0m\n";
        }
    } else {
        o += "  Press SPACE to start this block\n";
    }
    o + &format!("\n  {}\n\n  SPACE/n next   r redo   s skip   p pause   b break (save + rest)   q finish\n", b.instruction)
}

/// Next block-time source: the stream's device clock, extrapolated by the wall clock between packets
struct DevClock {
    first: Option<u32>,
    last: Option<(u32, i64)>,
    at: Instant,
}

impl DevClock {
    /// Unwraps `t32` against the previous packet, like `packet::parse`
    fn push(&mut self, t32: u32) -> i64 {
        let t = match self.last {
            None => {
                self.first = Some(t32);
                i64::from(t32)
            }
            Some((pt, pu)) => pu + i64::from(t32.wrapping_sub(pt) as i32),
        };
        self.last = Some((t32, t));
        self.at = Instant::now();
        t
    }

    fn now(&self) -> Option<i64> {
        self.last.map(|(_, t)| t + self.at.elapsed().as_micros() as i64)
    }
}

/// How a run of blocks in one part ended
enum Stop {
    /// All blocks done (or q / Ctrl-C): save the last part and finish
    Finished,
    /// A part boundary between blocks (flash filling up): save, record a new part, go on
    Split,
    /// `b`: save, leave the pod idle until SPACE
    Break,
}

struct PartRun {
    d: Dev,
    notes: Notes,
    stop: Stop,
    /// Stream markers (device t_us, source)
    markers: Vec<(i64, &'static str)>,
}

struct Cfg {
    player: String,
    part: u32,
    /// Debug `--part-every-blocks`
    every: Option<u32>,
    /// Session start (the screen's clock)
    t0: Instant,
}

/// Packets stored in the pod's flash and whether it is full: from the status, else estimated from the stream clock
async fn stored_packets(d: &Dev, clk: &DevClock) -> (u32, bool) {
    match tokio::time::timeout(Duration::from_secs(3), d.status()).await {
        Ok(Ok(s)) => (s.buffered_packets, s.state_bits & state::FLASH_FULL != 0),
        _ => (estimated_packets(clk), false),
    }
}

fn estimated_packets(clk: &DevClock) -> u32 {
    clk.now().zip(clk.first).map_or(0, |(now, f)| ((now - i64::from(f)) / 2000).max(0) as u32)
}

/// Runs blocks until the runner finishes or a part boundary is due. Streams for the counter (and the window); the recording on
/// the pod is the data. Block ends at the boundary: `Next` is turned into a pause so no block spans two parts.
async fn run_blocks(
    mut d: Dev,
    mut notes: Notes,
    runner: &mut Runner,
    keys: &mut UnboundedReceiver<u8>,
    cfg: &Cfg,
    data: &SyncSender<Vec<[u8; PACKET_LEN]>>,
    env: &mut Env,
) -> anyhow::Result<PartRun> {
    let mut clk = DevClock { first: None, last: None, at: Instant::now() };
    let (mut counter, mut markers) = (SwingCounter::new(THRESHOLD_DPS), Vec::new());
    let (mut last_data, mut tick) = (Instant::now(), tokio::time::interval(Duration::from_millis(250)));
    let (mut link, mut stop, mut flash_full) = (String::new(), None, false);
    let blocks_in_part = |r: &Runner| r.records().iter().filter(|b| b.part == cfg.part).count() as u32;
    let next_est = |r: &Runner| r.upcoming().map_or(0, parts::expected_packets);
    let apply = |effects: Vec<Effect>, d: &Dev| {
        for e in effects {
            match e {
                Effect::Bell => print!("\x07"),
                Effect::Marker => {
                    let c = d.p.clone();
                    let ctl = d.control.clone();
                    tokio::spawn(async move {
                        // Hangs on a dead link
                        if !matches!(tokio::time::timeout(Duration::from_secs(3), c.write(&ctl, &[op::MARK], WriteType::WithResponse)).await, Ok(Ok(()))) {
                            eprintln!("marker not sent");
                        }
                    });
                }
            }
        }
    };
    while !runner.finished() && stop.is_none() {
        tokio::select! {
            n = notes.next() => match n {
                None => last_data = Instant::now() - STALL * 2,
                Some(n) if n.uuid == u(gatt::IMU_STREAM) => {
                    last_data = Instant::now();
                    let batch = n.value.as_chunks::<PACKET_LEN>().0.to_vec();
                    for p in &batch {
                        let pk = oso_proto::Packet::decode(p);
                        let t = clk.push(pk.t_us);
                        counter.threshold = runner.threshold(THRESHOLD_DPS);
                        if counter.push(&packet::to_sample(t, pk.v)) {
                            apply(runner.swing(), &d);
                        }
                    }
                    data.try_send(batch).ok();
                }
                Some(n) => {
                    if let Some(s) = Status::decode(&n.value) {
                        if let Some(src) = marker_source(s.event) {
                            markers.push((i64::from(s.event_value), src));
                        }
                        flash_full |= s.state_bits & state::FLASH_FULL != 0;
                    }
                }
            },
            k = keys.next() => match k {
                Some(b) => if let Some(key) = Key::from_byte(b) {
                    if let Some(now) = clk.now() {
                        let fx = match key {
                            Key::Break => {
                                stop = Some(Stop::Break);
                                runner.key(Key::Pause, now)
                            }
                            Key::Next => {
                                let (stored, full) = stored_packets(&d, &clk).await;
                                flash_full |= full;
                                let n = blocks_in_part(runner) + u32::from(runner.is_active());
                                if parts::should_split(stored, flash_full, next_est(runner), n, cfg.every, true) {
                                    stop = Some(Stop::Split);
                                    runner.key(Key::Pause, now)
                                } else {
                                    runner.key(Key::Next, now)
                                }
                            }
                            k => runner.key(k, now),
                        };
                        apply(fx, &d);
                    } else {
                        eprintln!("waiting for the first stream data");
                    }
                },
                // stdin closed (a pipe): carry on, the timed blocks and Ctrl-C still work
                None => tokio::time::sleep(Duration::from_millis(250)).await,
            },
            _ = tick.tick() => {
                if last_data.elapsed() > STALL {
                    link = "LINK LOST: reconnecting (counting paused)".into();
                    print!("{}", screen(&runner.view(clk.now().unwrap_or(0)), &cfg.player, cfg.t0.elapsed().as_secs_f64(), &link));
                    tokio::time::timeout(Duration::from_secs(3), d.p.disconnect()).await.ok();
                    // The pod keeps recording on its own; only the live stream is re-opened. Keys wait until it is back.
                    (d, notes) = retry(open, RETRY_WAIT).await;
                    link.clear();
                    last_data = Instant::now();
                }
                if let Some(now) = clk.now() {
                    apply(runner.tick(now), &d);
                    env.checkpoint(runner, cfg.part);
                    // Between blocks (a pause) and the flash is filling: do the boundary now rather than at the next SPACE
                    if !runner.is_active() && !runner.finished()
                        && parts::should_split(estimated_packets(&clk), flash_full, next_est(runner), blocks_in_part(runner), cfg.every, true)
                    {
                        stop = Some(Stop::Split);
                    }
                    let warn = if flash_full && runner.is_active() { "  FLASH FULL: finish this block; a new part follows" } else { "" };
                    let v = runner.view(now);
                    print!("{}", screen(&v, &cfg.player, cfg.t0.elapsed().as_secs_f64(), &format!("part {} {link}{warn}", cfg.part)));
                    if let Ok(mut t) = env.shared.title.lock() {
                        *t = format!("{} | part {} | SPACE next, r redo, s skip, p pause, b break (terminal), q finish{warn}", headline(&v), cfg.part);
                    }
                } else {
                    println!("\x1b[H\x1b[Jwaiting for the stream...");
                }
                std::io::Write::flush(&mut std::io::stdout()).ok();
            },
            _ = tokio::signal::ctrl_c() => apply(runner.key(Key::Quit, clk.now().unwrap_or(0)), &d),
        }
    }
    // Let the last marker write go out before the recording is stopped
    tokio::time::sleep(Duration::from_millis(300)).await;
    let stop = if runner.finished() { Stop::Finished } else { stop.unwrap_or(Stop::Finished) };
    Ok(PartRun { d, notes, stop, markers })
}

/// The optional free-text prompt at the end (the terminal is back in line mode; an empty or closed stdin skips it)
async fn operator_notes(keys: &mut UnboundedReceiver<u8>) -> String {
    while keys.try_recv().is_ok() {}
    eprint!("Operator notes (optional, Enter to skip): ");
    let mut line = Vec::new();
    let read = async {
        while let Some(b) = keys.next().await {
            if b == b'\n' || b == b'\r' {
                break;
            }
            line.push(b);
        }
    };
    tokio::time::timeout(Duration::from_secs(120), read).await.ok();
    String::from_utf8_lossy(&line).trim().to_string()
}

fn unix_now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

/// A fresh connection with both characteristics subscribed, nothing started
async fn open_quiet() -> anyhow::Result<(Dev, Notes)> {
    let d = connect().await?;
    let setup = async {
        d.p.subscribe(&d.stream).await?;
        d.p.subscribe(&d.status).await?;
        anyhow::Ok(d.p.notifications().await?)
    };
    match setup.await {
        Ok(notes) => Ok((d, notes)),
        Err(e) => {
            tokio::time::timeout(Duration::from_secs(3), d.p.disconnect()).await.ok();
            Err(e)
        }
    }
}

/// 0x03, then waits out the erase (a few seconds to ~20 s, when the link may drop: it is re-opened) until the pod reports
/// recording, then starts the live stream
async fn start_recording(mut d: Dev, mut notes: Notes, force: bool) -> anyhow::Result<(Dev, Notes)> {
    if run_cmd(&d, &mut notes, op::START_REC, force).await? {
        d.p.disconnect().await.ok();
        bail!("recording not started");
    }
    let (t0, mut resent) = (Instant::now(), false);
    loop {
        match tokio::time::timeout(Duration::from_secs(5), d.status()).await {
            Ok(Ok(s)) if s.state_bits & state::RECORDING != 0 && s.state_bits & state::ERASING == 0 => break,
            // Neither erasing nor recording after a while: the command was lost with the link
            Ok(Ok(s)) if s.state_bits & (state::RECORDING | state::ERASING) == 0 && t0.elapsed() > Duration::from_secs(6) && !resent => {
                resent = true;
                d.cmd(op::START_REC).await.ok();
            }
            Ok(Ok(_)) => {}
            _ => {
                eprintln!("[{}] link dropped while the pod erases; reconnecting", stamp());
                tokio::time::timeout(Duration::from_secs(3), d.p.disconnect()).await.ok();
                (d, notes) = retry(open_quiet, RETRY_WAIT).await;
            }
        }
        anyhow::ensure!(t0.elapsed() < Duration::from_secs(120), "the pod did not start recording within 2 minutes");
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    d.cmd(op::START_STREAM).await?;
    Ok((d, notes))
}

/// 0x02 + 0x04, and the pod must report recording stopped; the link is closed (the upload opens its own)
async fn stop_recording(d: Dev, mut notes: Notes) -> anyhow::Result<()> {
    tokio::time::timeout(Duration::from_secs(3), d.cmd(op::STOP_STREAM)).await.ok();
    run_cmd(&d, &mut notes, op::STOP_REC, false).await?;
    let after = tokio::time::timeout(Duration::from_secs(5), d.status()).await.context("pod status timed out")??;
    tokio::time::timeout(Duration::from_secs(3), d.p.disconnect()).await.ok();
    anyhow::ensure!(after.state_bits & state::RECORDING == 0, "the pod is still recording: `oso-rec record stop`");
    Ok(())
}

/// The session folder and its metadata
struct Env {
    dir: PathBuf,
    meta: Session,
    shared: Arc<Shared>,
}

impl Env {
    fn write(&self) -> anyhow::Result<()> {
        // Temp file + rename: a crash mid-write leaves the previous session.json, never a torn one
        let (tmp, dst) = (self.dir.join("session.json.tmp"), self.dir.join("session.json"));
        std::fs::write(&tmp, self.meta.to_json()).and_then(|()| std::fs::rename(&tmp, &dst)).with_context(|| format!("writing {}", dst.display()))
    }

    /// Saves `part`'s block records as they stand, when they changed: a crash mid-part keeps every finished block's times
    fn checkpoint(&mut self, runner: &Runner, part: u32) {
        let now: Vec<_> = runner.records().iter().filter(|b| b.part == part).collect();
        if now.len() == self.meta.blocks.iter().filter(|b| b.part == part).count() && now.iter().all(|r| self.meta.blocks.contains(r)) {
            return;
        }
        self.sync_blocks(runner, part);
        if let Err(e) = self.write() {
            eprintln!("could not checkpoint session.json: {e:#}");
        }
    }

    /// A status line for the terminal and the window title
    fn say(&self, text: &str) {
        println!("\x1b[H\x1b[J\n  {text}\n");
        if let Ok(mut t) = self.shared.title.lock() {
            *t = text.to_string();
        }
    }

    /// `part`'s block records from the runner (the earlier parts' stay as stored, already normalised)
    fn sync_blocks(&mut self, runner: &Runner, part: u32) {
        self.meta.blocks.retain(|b| b.part != part);
        self.meta.blocks.extend(runner.records().iter().filter(|b| b.part == part).cloned());
    }
}

/// (first, last) packet `t_us` of a file
fn span(c: &packet::Capture) -> (i64, i64) {
    let us = |s: Option<&packet::Sample>| s.map_or(0, |s| (s.t_dev * 1e6).round() as i64);
    (us(c.samples.first()), us(c.samples.last()))
}

/// Downloads `part` (verified; refused when its time range contradicts the part's blocks) into the folder and records it in
/// session.json. The pod must have stopped recording. session.json is written first, so a failed upload loses nothing.
async fn upload_part(env: &mut Env, part: u32, live: &[(i64, &'static str)]) -> anyhow::Result<()> {
    env.meta.markers.retain(|m| m.part != part);
    env.meta.markers.extend(live.iter().map(|&(t, s)| MarkerRec { part, t_us: t, source: s.into() }));
    env.meta.open_part = Some(part);
    env.write()?;
    let file = parts::part_file(part);
    let path = env.dir.join(&file);
    let blocks = env.meta.blocks.clone();
    let info = std::sync::Mutex::new((0i64, 0i64, 0usize));
    let check = |c: &packet::Capture| {
        let (f, l) = span(c);
        *info.lock().unwrap() = (f, l, c.stats.arrivals);
        anyhow::ensure!(parts::part_matches(&blocks, part, f, l), "this recording (t_us {f}..{l}) does not contain part {part}'s blocks: it is another session's");
        Ok(())
    };
    match upload_checked(path.clone(), &check).await {
        Ok(stored) => {
            let (f, l, n) = *info.lock().unwrap();
            std::fs::remove_file(path.with_extension("markers.json")).ok();
            for b in env.meta.blocks.iter_mut().filter(|b| b.part == part) {
                b.t_start_us = b.t_start_us.map(|t| parts::unwrap_to(t, f));
                b.t_end_us = b.t_end_us.map(|t| parts::unwrap_to(t, f));
            }
            // The pod's own record of the markers (taps and commands) replaces what the live stream saw
            env.meta.markers.retain(|m| m.part != part);
            env.meta.markers.extend(stored.iter().map(|&(t, s)| MarkerRec { part, t_us: parts::unwrap_to(i64::from(t), f), source: s.into() }));
            env.meta.parts.push(PartRec { index: part, file, packets: n as u64, t_first_us: f, t_last_us: l });
            env.meta.open_part = None;
            env.write()
        }
        Err(e) => {
            eprintln!("\nUPLOAD FAILED: {e:#}");
            eprintln!("The pod still holds part {part} (not marked uploaded); the block times are in {}/session.json.", env.dir.display());
            bail!("recover with: oso-rec collect --resume {}", env.dir.display())
        }
    }
}

/// During a break: true on SPACE/n, false on q (or a closed stdin or Ctrl-C)
async fn wait_for_space(keys: &mut UnboundedReceiver<u8>) -> bool {
    loop {
        tokio::select! {
            k = keys.next() => match k.and_then(|b| Some(Key::from_byte(b)).map(|k| (b, k))) {
                Some((_, Some(Key::Next))) => return true,
                Some((_, Some(Key::Quit))) | None => return false,
                _ => {}
            },
            _ = tokio::signal::ctrl_c() => return false,
        }
    }
}

struct Loop {
    env: Env,
    runner: Runner,
    keys: UnboundedReceiver<u8>,
    data: SyncSender<Vec<[u8; PACKET_LEN]>>,
    every: Option<u32>,
    t0: Instant,
    tty: Option<RawTty>,
}

/// Parts until the blocks are done: run, (prompt notes at the end), stop, save, then a new recording unless finished; a
/// break waits for SPACE with the pod idle.
async fn session_loop(mut l: Loop, mut d: Dev, mut notes: Notes, mut part: u32) -> anyhow::Result<()> {
    let player = l.env.meta.player.id.clone();
    let finish_notes = |l: &mut Loop| {
        drop(l.tty.take());
        println!("\x1b[H\x1b[J");
    };
    loop {
        l.env.meta.open_part = Some(part);
        l.env.write()?;
        l.runner.set_part(part);
        let cfg = Cfg { player: player.clone(), part, every: l.every, t0: l.t0 };
        let PartRun { d: dd, notes: nn, stop, markers } = run_blocks(d, notes, &mut l.runner, &mut l.keys, &cfg, &l.data, &mut l.env).await?;
        let last = matches!(stop, Stop::Finished);
        if last {
            finish_notes(&mut l);
            let n = operator_notes(&mut l.keys).await;
            if !n.is_empty() {
                let o = &mut l.env.meta.operator_notes;
                *o = if o.is_empty() { n } else { format!("{o}\n{n}") };
            }
        }
        l.env.say(&format!("saving part {part}... (about 30 s, keep the racquet still)"));
        l.env.sync_blocks(&l.runner, part);
        l.env.write()?;
        stop_recording(dd, nn).await?;
        upload_part(&mut l.env, part, &markers).await?;
        if last {
            break;
        }
        if let Stop::Break = stop {
            l.env.say(&format!("ON BREAK: part {part} saved, the pod is idle. Press SPACE to continue (q to finish the session)"));
            if !wait_for_space(&mut l.keys).await {
                finish_notes(&mut l);
                let n = operator_notes(&mut l.keys).await;
                if !n.is_empty() {
                    l.env.meta.operator_notes = n;
                }
                l.env.write()?;
                break;
            }
        }
        part += 1;
        l.env.say(&format!("starting part {part}... (the pod erases its flash first, up to ~20 s)"));
        let (dd, nn) = retry(open_quiet, RETRY_WAIT).await;
        (d, notes) = start_recording(dd, nn, false).await?;
    }
    l.env.shared.done.store(true, Ordering::Relaxed);
    let m = &l.env.meta;
    let secs = m.blocks.iter().filter(|b| b.status != BlockStatus::Discarded).filter_map(|b| Some(b.t_end_us? - b.t_start_us?)).sum::<i64>() as f64 * 1e-6;
    println!(
        "{} block attempts in {} part(s) ({} skipped, {} discarded), {} reps counted, {} in blocks\nsession: {}",
        m.blocks.iter().filter(|b| b.t_start_us.is_some()).count(),
        m.parts.len(),
        m.blocks.iter().filter(|b| b.status == BlockStatus::Skipped).count(),
        m.blocks.iter().filter(|b| b.status == BlockStatus::Discarded).count(),
        l.runner.total_counted(),
        clock(secs),
        l.env.dir.display()
    );
    Ok(())
}

async fn collect(a: CollectArgs) -> anyhow::Result<()> {
    let resumed = match &a.resume {
        Some(dir) => {
            let m = Session::from_json(&std::fs::read_to_string(dir.join("session.json")).with_context(|| format!("reading {}/session.json", dir.display()))?)?;
            anyhow::ensure!(m.schema == session::SCHEMA, "only /2 sessions (recorded in parts) can be resumed");
            Some((dir.clone(), m))
        }
        None => None,
    };
    let which = match &resumed {
        Some((_, m)) if protocol::builtin(&m.protocol.name).is_some() => m.protocol.name.clone(),
        _ => a.protocol.clone(),
    };
    let text = match protocol::builtin(&which) {
        Some(t) => t.to_string(),
        None => std::fs::read_to_string(&which).with_context(|| format!("protocol {which:?}: not built in (drills-v1, smoke) and not a file (use --protocol FILE)"))?,
    };
    let proto = Protocol::parse(&text)?;
    if let Some((_, m)) = &resumed {
        anyhow::ensure!((m.protocol.name.as_str(), m.protocol.version) == (proto.name.as_str(), proto.version), "the session used protocol {} v{}, not {} v{}", m.protocol.name, m.protocol.version, proto.name, proto.version);
    }
    let root = match &a.out {
        Some(o) => o.clone(),
        None => PathBuf::from(std::env::var_os("HOME").context("no HOME")?).join("Datasets/oso-strokes/raw"),
    };
    // The mount makes every later analysis possible: no session without one
    let mount_path = Mount::default_path().context("no HOME")?;
    if !mount_path.exists() {
        eprintln!("No mount calibration at {}.", mount_path.display());
        anyhow::ensure!(ask("Calibrate now (two poses, Enter to go)? [y/N] ").eq_ignore_ascii_case("y"), "collect needs a mount calibration (`oso-rec calibrate`)");
        calibrate().await?;
    }
    // Every new session starts with a fresh decision: pods get remounted and a stale mount silently ruins the session
    if resumed.is_none() && mount_path.exists() {
        let age = std::fs::metadata(&mount_path).and_then(|m| m.modified()).ok().and_then(|t| t.elapsed().ok()).map_or(String::new(), |e| format!(" (calibrated {} min ago)", e.as_secs() / 60));
        if ask(&format!("Saved mount{age}. Enter = keep it, C = calibrate now: ")).eq_ignore_ascii_case("c") {
            calibrate().await?;
        }
    }
    let mount_calibrated_at = std::fs::metadata(&mount_path).and_then(|m| m.modified()).ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d| session::iso_utc(d.as_secs()));
    let mount = Mount::load(&mount_path)?;
    let axes: MountAxes = serde_json::from_str(&mount.to_json())?;
    if let Some((_, m)) = &resumed {
        let same = (0..3).all(|i| (m.mount.shaft[i] - axes.shaft[i]).abs() < 1e-6 && (m.mount.normal[i] - axes.normal[i]).abs() < 1e-6);
        anyhow::ensure!(same, "~/.config/oso/mount.json differs from this session's mount: one session needs one mount (restore the file, or start a new session)");
    }

    let d = connect().await?;
    let st = d.status().await?;
    eprintln!("{}", describe(&st));
    anyhow::ensure!(st.state_bits & state::IMU_OK != 0, "the pod reports no working IMU (imu_ok clear)");
    anyhow::ensure!(st.state_bits & state::UPLOADING == 0, "the pod is uploading");
    let (pending, recording) = (st.state_bits & state::PENDING_UPLOAD != 0, st.state_bits & state::RECORDING != 0);
    // ponytail: framing_resets (Phase 16.0, #27) is not in Status on this base; add the warning when it lands
    d.p.subscribe(&d.stream).await?;
    d.p.subscribe(&d.status).await?;
    let notes: Notes = d.p.notifications().await?;
    let shared = Arc::new(Shared { title: std::sync::Mutex::new(String::new()), done: Default::default() });

    let (env, seed, force, d, notes) = match resumed {
        None => {
            anyhow::ensure!(!recording, "the pod is already recording: `oso-rec record stop` first");
            if pending {
                anyhow::ensure!(a.force, "the pod holds an un-uploaded session: `oso-rec upload -o FILE` it first, or pass --force to overwrite it");
                anyhow::ensure!(ask("This ERASES the pod's un-uploaded session. Type 'yes' to overwrite: ") == "yes", "not confirmed");
            }
            let started = unix_now();
            let seed = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(1, |d| d.as_nanos() as u64) & ((1 << 48) - 1);
            let dir = root.join(session::folder_name(started, &a.player));
            std::fs::create_dir_all(&dir).with_context(|| format!("creating {}", dir.display()))?;
            let pod = Pod {
                mac: if d.p.address().to_string().replace([':', '0'], "").is_empty() { d.p.id().to_string() } else { d.p.address().to_string() },
                fw: format!("{}.{}", st.fw_major, st.fw_minor),
            };
            let meta = Session {
                schema: session::SCHEMA.into(),
                player: Player { id: a.player.clone(), handed: a.handed, level: a.level, height_bin: a.height_bin.clone() },
                protocol: ProtocolRef { name: proto.name.clone(), version: proto.version, seed },
                pod,
                mount: axes,
                mount_calibrated_at,
                context: a.context,
                started_at: session::iso_utc(started),
                parts: Vec::new(),
                open_part: None,
                blocks: Vec::new(),
                markers: Vec::new(),
                operator_notes: String::new(),
            };
            (Env { dir, meta, shared: shared.clone() }, seed, pending && a.force, d, notes)
        }
        Some((dir, meta)) => {
            let seed = meta.protocol.seed;
            let mut env = Env { dir, meta, shared: shared.clone() };
            let (d, notes) = match parts::resume_plan(&env.meta, pending, recording) {
                parts::ResumePlan::Refuse(why) => {
                    d.p.disconnect().await.ok();
                    bail!("{why}");
                }
                parts::ResumePlan::Fresh => (d, notes),
                parts::ResumePlan::UploadOpen(n) => {
                    eprintln!("the pod still holds part {n} of this session: saving it into {} first", env.dir.display());
                    if recording {
                        stop_recording(d, notes).await?;
                    } else {
                        d.p.disconnect().await.ok();
                    }
                    upload_part(&mut env, n, &[]).await?;
                    open_quiet().await?
                }
            };
            (env, seed, false, d, notes)
        }
    };
    let part = parts::next_part(&env.meta);
    let blocks = proto.order(seed);
    let runner = Runner::resume(blocks.clone(), env.meta.blocks.clone(), part);
    anyhow::ensure!(!runner.finished(), "every block of this session is done");
    eprintln!("{} blocks, starting at {}; part {part} records on the pod. SPACE starts the block.", blocks.len(), runner.view(0).idx + 1);
    let (d, notes) = start_recording(d, notes, force).await?;

    let (data_tx, data_rx) = sync_channel(DATA_BATCHES);
    let tty = Some(RawTty::enter());
    let keys = stdin_bytes();
    let l = Loop { env, runner, keys, data: data_tx, every: a.part_every_blocks, t0: Instant::now(), tty };
    let task = tokio::spawn(session_loop(l, d, notes, part));
    if a.view {
        let view = CollectView { data: data_rx, fusion: LiveFusion::with_mount(mount), shared: shared.clone(), shown: String::new() };
        // A window that fails to open must not end the session: the terminal still drives it
        if let Err(e) = oso_render::live::run_live(Box::new(view), None) {
            eprintln!("live window unavailable ({e}); continuing in the terminal");
        }
    }
    task.await?
}

/// `profile SESSION_DIR|imu.bin ... -o my_style.json [--mount identity|flat|FILE]`: the Volante Twins style profile (docs/twins-app.md).
/// A session folder uses its own mount; the flag applies to bare imu.bin files only.
fn profile(args: &[String]) -> anyhow::Result<()> {
    let (mut paths, mut out, mut mount) = (Vec::new(), None, None);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "-o" => out = it.next(),
            "--mount" => mount = it.next(),
            f if f.starts_with('-') => bail!("unknown flag {f}\n{USAGE}"),
            p => paths.push(Path::new(p)),
        }
    }
    let out = out.context(USAGE)?;
    anyhow::ensure!(!paths.is_empty(), USAGE);
    let mount = Mount::resolve(mount.map(String::as_str))?;
    let inputs = paths.iter().map(|p| profile::load(p, &mount)).collect::<anyhow::Result<Vec<_>>>()?;
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs());
    let p = profile::compute(&inputs, &session::iso_utc(now), concat!("oso-rec ", env!("CARGO_PKG_VERSION")))?;
    std::fs::write(out, p.to_json()).with_context(|| format!("writing {out}"))?;
    println!(
        "wrote {out}: {} swings in {} sessions ({} min), head speed p50 {} p90 {} km/h, median gap {} s",
        p.source.swings, p.source.sessions, p.source.minutes, p.speed.p50_kmh, p.speed.p90_kmh, p.tempo.median_gap_s
    );
    Ok(())
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let flag = |f: &str| args.iter().position(|a| a == f).and_then(|i| args.get(i + 1));
    // `--secs N` closes the live window by itself (tests, hardware checks)
    let live_secs = flag("--secs").and_then(|s| s.parse::<f64>().ok());
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
            Some("start") => match record_start_flags(&args) {
                (force, true) => live(Some(force), live_secs, Mount::resolve(flag("--mount").map(String::as_str))?).await,
                (force, false) => simple(op::START_REC, force).await,
            },
            Some("stop") => simple(op::STOP_REC, false).await,
            _ => bail!(USAGE),
        },
        Some("live") => match flag("--replay-file") {
            Some(f) => live_file(Path::new(f), flag("--time").map(|t| t.parse()).transpose()?, flag("--screenshot").map(PathBuf::from), Mount::resolve(flag("--mount").map(String::as_str))?),
            None => live(None, live_secs, Mount::resolve(flag("--mount").map(String::as_str))?).await,
        },
        Some("calibrate") => calibrate().await,
        Some("collect") => collect(CollectArgs::parse(&args[1..])?).await,
        Some("sleep") => sleep().await,
        Some("wake") => wake().await,
        Some("stream") => stream(out()?, flag("--secs").map(|s| s.parse()).transpose()?).await,
        Some("upload") => upload(out()?).await.map(|_| ()),
        Some("profile") => profile(&args[1..]),
        _ => bail!(USAGE),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(s: &str) -> Vec<String> {
        s.split_whitespace().map(String::from).collect()
    }

    #[test]
    fn record_start_flags_parse() {
        assert_eq!(record_start_flags(&args("record start")), (false, true));
        assert_eq!(record_start_flags(&args("record start --force")), (true, true));
        assert_eq!(record_start_flags(&args("record start --no-view")), (false, false));
        assert_eq!(record_start_flags(&args("record start --no-view --force")), (true, false));
    }

    #[tokio::test]
    async fn retry_keeps_trying_until_it_connects() {
        let mut tries = 0;
        let v = retry(
            async || {
                tries += 1;
                if tries < 3 { Err(anyhow::anyhow!("no device")) } else { Ok(tries) }
            },
            Duration::from_millis(1),
        )
        .await;
        assert_eq!(v, 3);
        assert_eq!(stamp().len(), 9);
    }

    #[test]
    fn screen_shows_block_side_count_and_done() {
        use oso_replay::collect::protocol::Block;
        let b = Block { label: "clear".into(), side: Some("FH".into()), target: 25, secs: None, instruction: "Hit to the back line.".into(), optional: false, count_threshold_dps: None };
        let mut r = Runner::new(vec![b]);
        assert!(screen(&r.view(0), "P07", 0.0, "").contains("Press SPACE to start"));
        r.key(Key::Next, 0);
        (0..25).for_each(|_| drop(r.swing()));
        let s = screen(&r.view(65_000_000), "P07", 70.0, "");
        for want in ["BLOCK 1 / 1", "FORWARD", "CLEAR", "25 / 25 reps", "1:05", "DONE", "Hit to the back line.", "P07"] {
            assert!(s.contains(want) || want == "FORWARD" && s.contains("FOREHAND"), "{want}: {s}");
        }
    }

    #[test]
    fn dev_clock_unwraps_u32() {
        let mut c = DevClock { first: None, last: None, at: Instant::now() };
        assert_eq!(c.push(u32::MAX - 1_000), i64::from(u32::MAX) - 1_000);
        assert_eq!(c.push(1_000), i64::from(u32::MAX) + 1_001);
        assert_eq!(c.first, Some(u32::MAX - 1_000));
        assert!(c.now().unwrap() >= i64::from(u32::MAX) + 1_001);
    }

    #[test]
    fn title_shows_state_rate_and_loss() {
        let info = |connected, bits, calibrating, hz, loss_pct, dropped, prompt| TitleInfo { connected, state_bits: bits, calibrating, hz, loss_pct, dropped, prompt };
        let t = title(&info(true, state::RECORDING | state::STREAMING, false, 498.7, 0.25, 0, ""));
        assert!(t.contains("recording + streaming") && t.contains("499 Hz") && t.contains("loss 0.2 %") && t.contains("R re-centre"));
        assert!(title(&info(false, 0, false, 0.0, 0.0, 0, "")).contains("reconnecting…"));
        assert!(title(&info(true, 1, true, 0.0, 0.0, 7, "C: go")).starts_with("C: go | "));
        assert!(title(&info(true, 1, true, 0.0, 0.0, 7, "")).contains("hold still") && title(&info(true, 1, true, 0.0, 0.0, 7, "")).contains("7 dropped"));
    }
}
