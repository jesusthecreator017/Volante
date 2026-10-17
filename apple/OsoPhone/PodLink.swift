// The racquet pod over CoreBluetooth. Same flows as the web app (app/src/app.ts), same contract
// (docs/ble-profile.md): binary status, events only as notifications, record confirm, upload handshake with
// mark-uploaded, sleep/wake. Protocol details live in OsoKit (PodProtocol.swift, ImuFile.swift).

@preconcurrency import CoreBluetooth
import Foundation
import Observation
import OsoKit

// Main-actor by design: the central runs on the main queue and every timer is a main-actor task, so all state
// (waiters, collectors, @Observable properties) is touched from one place. The CoreBluetooth delegate methods are
// nonisolated and re-enter the actor with assumeIsolated, which is correct because of `queue: .main` below.
@MainActor
@Observable
final class PodLink: NSObject {
    enum State: String { case off, idle, scanning, connecting, connected }

    private(set) var state: State = .off
    private(set) var found: [CBPeripheral] = []
    private(set) var peripheralName: String?
    private(set) var status: PodStatus?
    private(set) var battery: Int?
    private(set) var rssi: Int?
    private(set) var mtu: Int?
    /// one-line info for the Device screen
    var notice: String?
    var lastError: String?

    /// live stream: analyzer + the packets for imu.bin
    private(set) var live: LiveAnalyzer?
    private(set) var liveSwings: [Swing] = []
    private(set) var livePackets = 0
    private(set) var uploadReceived = 0
    private(set) var uploadExpected = 0
    var uploading: Bool { upload != nil }
    var streaming: Bool { stream != nil }

    /// Asked before overwriting an un-uploaded session (the UI shows an alert and calls the continuation).
    var confirmOverwrite: (() async -> Bool)?
    /// Called with each finished session (stream stop or upload_done).
    var onSession: ((_ imu: [UInt8], _ meta: SessionMeta) -> Void)?

    @ObservationIgnored private var central: CBCentralManager!
    @ObservationIgnored private var pod: CBPeripheral?
    @ObservationIgnored private var control: CBCharacteristic?
    @ObservationIgnored private var statusChar: CBCharacteristic?
    private var stream: Collector?
    private var upload: Collector?
    @ObservationIgnored private var anchor: ClockAnchor?
    @ObservationIgnored private var waiters: [(PodStatus) -> Bool] = []
    @ObservationIgnored private var stallTimer: Timer?

    /// Packets in arrival order (exactly what imu.bin stores), unwrapped for live analysis.
    private final class Collector {
        var packets: [RawPacket] = []
        var markers: [(UInt32, Bool)] = []
        var unwrap = Unwrapper()
        var lastData = Date()
    }

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: .main)
    }

    // MARK: connection

    func scan() {
        guard central.state == .poweredOn else { lastError = "Bluetooth is off or not allowed"; return }
        found = []
        state = .scanning
        // A sleeping pod advertises 2 s every 30 s: scan long enough to catch it
        central.scanForPeripherals(withServices: [CBUUID(string: PodUUID.service)])
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(40))
            guard let self, self.state == .scanning else { return }
            self.central.stopScan()
            self.state = .idle
        }
    }

    func connect(_ p: CBPeripheral) {
        central.stopScan()
        pod = p
        p.delegate = self
        state = .connecting
        peripheralName = p.name
        central.connect(p)
    }

    func disconnect() {
        if let pod { central.cancelPeripheralConnection(pod) }
    }

    // MARK: commands

    func send(_ cmd: PodCommand) {
        guard let pod, let control else { lastError = "not connected"; return }
        if status?.sleeping == true && cmd != .wake { lastError = PodError.sleeping.description; return }
        pod.writeValue(Data([cmd.rawValue]), for: control, type: .withResponse)
        if let statusChar { pod.readValue(for: statusChar) }
    }

    /// First status event matching `pred` within `seconds`, else nil.
    private func waitEvent(_ seconds: Double, _ pred: @escaping (PodStatus) -> Bool) async -> PodStatus? {
        await withCheckedContinuation { (k: CheckedContinuation<PodStatus?, Never>) in
            var done = false
            let w: (PodStatus) -> Bool = { s in
                guard !done, pred(s) else { return false }
                done = true
                k.resume(returning: s)
                return true
            }
            waiters.append(w)
            Task {
                try? await Task.sleep(for: .seconds(seconds))
                guard !done else { return }
                done = true
                k.resume(returning: nil)
            }
        }
    }

    /// 0x03. An un-uploaded session needs 0x03 twice within 5 s, after asking the user.
    func startRecording() async {
        let confirm = Task { await waitEvent(1.5) { $0.error == .needConfirm } }
        send(.startRecording)
        guard await confirm.value != nil else { return }
        guard await confirmOverwrite?() == true else { return }
        send(.startRecording)
    }

    func sleep() {
        if streaming { stopStream(save: true) }
        send(.sleep)
        notice = "Pod going to sleep. To wake it, scan and connect: it advertises 2 s every 30 s."
    }

    // MARK: live stream

    func startStream(settings: AnalysisSettings) {
        stream = Collector()
        live = LiveAnalyzer(rateHz: Double(status?.rateHz ?? 500), mount: .racquet, settings: settings)
        liveSwings = []
        livePackets = 0
        send(.startStream)
    }

    func stopStream(save: Bool) {
        guard let c = stream else { return }
        send(.stopStream)
        stream = nil
        live = nil
        guard save, !c.packets.isEmpty else { return }
        finish(c, source: .podStream, note: nil)
    }

    // MARK: upload

    func startUpload() {
        if streaming { stopStream(save: true) }
        upload = Collector()
        uploadExpected = Int(status?.bufferedPackets ?? 0)
        uploadReceived = 0
        notice = nil
        send(.upload)
        stallTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            // Scheduled from the main run loop, so this fires on the main thread
            MainActor.assumeIsolated {
                guard let self, let u = self.upload, Date().timeIntervalSince(u.lastData) > 20 else { return }
                self.stallTimer?.invalidate()
                self.lastError = "upload stalled for 20 s: Finish now keeps what arrived (the pod still holds the session)"
            }
        }
    }

    /// Saves what arrived without telling the pod it was uploaded.
    func finishUploadEarly() {
        guard let u = upload else { return }
        upload = nil
        stallTimer?.invalidate()
        finish(u, source: .podUpload, note: "finished early, not marked uploaded")
    }

    private func uploadDone(count: UInt32) {
        guard let u = upload else { return }
        upload = nil
        stallTimer?.invalidate()
        let got = u.packets.count
        guard got == Int(count) else {
            finish(u, source: .podUpload, note: "incomplete: \(got) of \(count) packets")
            lastError = "upload: received \(got) packets, the pod says \(count). Not marking it uploaded; upload again."
            return
        }
        finish(u, source: .podUpload, note: nil)
        if count > 0 { send(.markUploaded) }
        notice = "Uploaded \(count) packets; the pod marked the session uploaded."
    }

    private func finish(_ c: Collector, source: SessionSource, note: String?) {
        let rate = status?.rateHz ?? 500
        // Marker t_us unwrapped against the newest packet (sessions are far shorter than the 71 min wrap)
        let markers = c.markers.compactMap { c.unwrap.peekTime($0.0) }.map { $0 * 1e-6 }
        let id = newSessionId()
        let meta = SessionMeta(
            id: id, name: "\(source == .podUpload ? "Uploaded" : "Session") \(Date().formatted(date: .abbreviated, time: .shortened))",
            createdAt: Date(), source: source, deviceName: peripheralName, anchor: anchor, markers: markers, note: note
        )
        onSession?(ImuFile.encode(rateHz: rate, packets: c.packets), meta)
    }

    // MARK: incoming

    fileprivate func onStatus(_ s: PodStatus, notification: Bool) {
        status = s
        if let b = s.battery { battery = b }
        guard notification, let ev = s.event, ev != .none else { return }
        let before = waiters.count
        waiters.removeAll { $0(s) }
        let awaited = waiters.count < before
        switch ev {
        case .markerTap, .markerCommand:
            if let c = upload ?? stream { c.markers.append((s.eventValue, ev == .markerTap)) }
            // The first marker after connecting pairs the pod clock with the phone clock
            if anchor == nil, let c = stream ?? upload, let t = c.unwrap.peekTime(s.eventValue) {
                anchor = ClockAnchor(deviceT: t * 1e-6, wall: Date().timeIntervalSince1970)
            } else if anchor == nil {
                pendingAnchor = (s.eventValue, Date().timeIntervalSince1970)
            }
        case .uploadDone:
            uploadDone(count: s.eventValue)
        case .error:
            if !awaited { lastError = "pod: \(s.error?.description ?? "error \(s.eventValue)")" }
        case .none:
            break
        }
    }

    /// Marker seen before any packet: resolved against the first packet of the next stream/upload.
    @ObservationIgnored private var pendingAnchor: (UInt32, Double)?

    fileprivate func onData(_ bytes: [UInt8]) {
        guard let c = upload ?? stream, let ps = ImuFile.notification(bytes) else { return }
        c.lastData = Date()
        c.packets += ps
        var samples: [Sample] = []
        for p in ps {
            let u = c.unwrap.next(p)
            if stream != nil { samples.append(Sample(tUs: u.tUs, v: p.v)) }
        }
        if let (t32, wall) = pendingAnchor, let t = c.unwrap.peekTime(t32) {
            anchor = ClockAnchor(deviceT: t * 1e-6, wall: wall)
            pendingAnchor = nil
        }
        if upload != nil {
            uploadReceived = c.packets.count
        } else if var a = live {
            // ponytail: the reorder buffer of the web app is skipped here; out-of-order packets just look like noise
            liveSwings += a.push(samples)
            live = a
            livePackets = c.packets.count
        }
    }
}

// MARK: CoreBluetooth delegates
// The central is created with queue: .main, so every callback arrives on the main thread: assumeIsolated checks that.

extension PodLink: CBCentralManagerDelegate, CBPeripheralDelegate {
    nonisolated func centralManagerDidUpdateState(_ c: CBCentralManager) {
        MainActor.assumeIsolated {
            state = c.state == .poweredOn ? .idle : .off
        }
    }

    nonisolated func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral, advertisementData: [String: Any], rssi: NSNumber) {
        MainActor.assumeIsolated {
            if !found.contains(where: { $0.identifier == p.identifier }) { found.append(p) }
        }
    }

    nonisolated func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        MainActor.assumeIsolated {
            mtu = p.maximumWriteValueLength(for: .withoutResponse) + 3
            p.discoverServices([CBUUID(string: PodUUID.service), CBUUID(string: PodUUID.batteryService)])
        }
    }

    nonisolated func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        let message = error?.localizedDescription ?? "could not connect"
        MainActor.assumeIsolated {
            state = .idle
            lastError = message
        }
    }

    nonisolated func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        MainActor.assumeIsolated {
            if upload != nil {
                lastError = "link lost during the upload: the pod keeps the session, upload again"
                finishUploadEarly()
            }
            if streaming { stopStream(save: true) }
            state = .idle
            control = nil
            statusChar = nil
            pod = nil
            anchor = nil
        }
    }

    nonisolated func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        for s in p.services ?? [] { p.discoverCharacteristics(nil, for: s) }
    }

    nonisolated func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        MainActor.assumeIsolated {
            for ch in s.characteristics ?? [] {
                switch ch.uuid {
                case CBUUID(string: PodUUID.imuStream): p.setNotifyValue(true, for: ch)
                case CBUUID(string: PodUUID.control): control = ch
                case CBUUID(string: PodUUID.status):
                    statusChar = ch
                    p.setNotifyValue(true, for: ch)
                    p.readValue(for: ch)
                case CBUUID(string: PodUUID.batteryLevel): p.readValue(for: ch)
                default: break
                }
            }
            guard control != nil, statusChar != nil, state == .connecting else { return }
            state = .connected
            p.readRSSI()
            Task { [weak self] in
                try? await Task.sleep(for: .milliseconds(500))
                guard let self, let st = self.status else { return }
                if st.sleeping {
                    // Caught the 2 s advert window: wake within the pod's 10 s, it resets into the normal boot
                    self.notice = "The pod was sleeping: sent wake. It restarts (~7 s); then connect again."
                    self.send(.wake)
                } else {
                    // Clock anchor for heart rate: the marker's t_us arrives with the phone time
                    self.send(.syncMark)
                }
            }
        }
    }

    nonisolated func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        guard let v = ch.value else { return }
        let uuid = ch.uuid
        MainActor.assumeIsolated {
            switch uuid {
            case CBUUID(string: PodUUID.imuStream): onData([UInt8](v))
            case CBUUID(string: PodUUID.status):
                // Reads always carry event = none, so treating every update as a possible event is safe
                if let s = PodStatus([UInt8](v)) { onStatus(s, notification: true) }
            case CBUUID(string: PodUUID.batteryLevel):
                if let b = v.first, b != 255 { battery = Int(b) }
            default: break
            }
        }
    }

    nonisolated func peripheral(_ p: CBPeripheral, didReadRSSI value: NSNumber, error: Error?) {
        let r = value.intValue
        MainActor.assumeIsolated {
            rssi = r
            Task { [weak p] in
                try? await Task.sleep(for: .seconds(2))
                p?.readRSSI()
            }
        }
    }
}
