// A HealthKit badminton workout that records the wrist IMU. CMBatchedSensorManager (watchOS 10+) only delivers
// high-rate motion while a workout session runs: device motion at 200 Hz in ~1 s batches. Samples become pod-format
// packets (OsoKit WristEncoder, mount 1), swings are counted live with the same detector as the phone, and at the
// end the imu.bin + heart rate go to the phone over WatchConnectivity. The session is mirrored to the iPhone
// (startMirroringToCompanionDevice): that launches Volante there in the background for its Live Activity, and either
// side can end the workout for both.

@preconcurrency import CoreMotion
import Foundation
@preconcurrency import HealthKit
import Observation
import OsoKit
@preconcurrency import WatchConnectivity

// Main-actor state. HealthKit and WatchConnectivity call their delegates on background queues, so those callbacks
// are nonisolated: they read what they need from the (non-Sendable) HealthKit objects, then hop to the main actor
// with plain values.
@MainActor
@Observable
final class WorkoutManager: NSObject {
    enum Phase { case idle, starting, running, ending, sent }

    private(set) var phase: Phase = .idle
    private(set) var swings = 0
    private(set) var hits = 0
    private(set) var lastSwingDps: Double?
    /// best hand speed this workout, m/s
    private(set) var bestSpeed: Double?
    /// the workout's start (the elapsed timer counts from here, also in Always On)
    private(set) var startDate = Date()
    /// the phone has the mirrored session (Live Activity on the iPhone)
    private(set) var mirrored = false
    /// running for a while without a heart-rate reading: HealthKit access is probably off
    private(set) var heartRateMissing = false
    /// the last workout's swings and length, for the start screen
    private(set) var lastResult: (swings: Int, duration: TimeInterval)?
    private(set) var heartRate: Double?
    private(set) var activeEnergy: Double = 0
    private(set) var elapsed: TimeInterval = 0
    private(set) var samples = 0
    var error: String?

    @ObservationIgnored private let health = HKHealthStore()
    @ObservationIgnored private var session: HKWorkoutSession?
    @ObservationIgnored private var builder: HKLiveWorkoutBuilder?
    @ObservationIgnored private let motion = CMBatchedSensorManager()
    @ObservationIgnored private var motionTask: Task<Void, Never>?
    @ObservationIgnored private var encoder = WristEncoder(rateHz: 200)
    @ObservationIgnored private var live = LiveAnalyzer(rateHz: 200, mount: .wrist, trustedBias: true)
    @ObservationIgnored private var hr: [HeartRateSample] = []
    /// CoreMotion timestamps are seconds since boot; Date = boot + timestamp
    @ObservationIgnored private let bootDate = Date(timeIntervalSinceNow: -ProcessInfo.processInfo.systemUptime)
    @ObservationIgnored private var ticker: Task<Void, Never>?

    override init() {
        super.init()
        if WCSession.isSupported() { WCSession.default.delegate = self; WCSession.default.activate() }
    }

    func requestAuthorization() async {
        // Sharing heart rate and energy too: the workout builder saves the samples it collects with the workout
        let share: Set<HKSampleType> = [HKObjectType.workoutType(), HKQuantityType(.heartRate), HKQuantityType(.activeEnergyBurned)]
        let read: Set<HKObjectType> = [HKQuantityType(.heartRate), HKQuantityType(.activeEnergyBurned), HKObjectType.workoutType()]
        do { try await health.requestAuthorization(toShare: share, read: read) } catch { self.error = error.localizedDescription }
    }

    // MARK: workout

    func start() async {
        #if targetEnvironment(simulator)
        // Simulators have no motion sensors: a demo source feeds synthetic swings through the same pipeline (see SimulatedMotion)
        let simulated = !CMBatchedSensorManager.isDeviceMotionSupported
        #else
        let simulated = false
        guard CMBatchedSensorManager.isDeviceMotionSupported else { error = "This watch has no batched device motion (watchOS 10+, Series 8 / Ultra or later)"; return }
        #endif
        phase = .starting
        swings = 0; hits = 0; lastSwingDps = nil; bestSpeed = nil; heartRate = nil; activeEnergy = 0; samples = 0; hr = []
        elapsed = 0; mirrored = false; heartRateMissing = false; error = nil
        encoder = WristEncoder(rateHz: 200)
        live = LiveAnalyzer(rateHz: 200, mount: .wrist, trustedBias: true)
        let config = HKWorkoutConfiguration()
        config.activityType = .badminton
        config.locationType = .indoor
        do {
            let s = try HKWorkoutSession(healthStore: health, configuration: config)
            let b = s.associatedWorkoutBuilder()
            b.dataSource = HKLiveWorkoutDataSource(healthStore: health, workoutConfiguration: config)
            s.delegate = self
            b.delegate = self
            session = s
            builder = b
            startDate = Date()
            s.startActivity(with: startDate)
            try await b.beginCollection(at: startDate)
            mirror(s)
        } catch {
            if !simulated {
                self.error = error.localizedDescription
                phase = .idle
                return
            }
            startDate = Date() // the simulator may refuse a workout session: the demo runs without one
        }
        phase = .running
        broadcast("started")
        #if targetEnvironment(simulator)
        if simulated { startSimulatedMotion() } else if session?.state == .running { startMotion() }
        #else
        // CMBatchedSensorManager only delivers while the workout session is running: wait for the delegate's .running
        // (workoutSession(_:didChangeTo:)), unless it is already there.
        if session?.state == .running { startMotion() }
        #endif
        startTicker()
    }

    /// Once a second on the main actor: elapsed time, and the live numbers to the phone.
    private func startTicker() {
        ticker?.cancel()
        ticker = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, !Task.isCancelled else { return }
                self.elapsed = Date().timeIntervalSince(self.startDate)
                if self.elapsed > 30, self.heartRate == nil, self.session != nil { self.heartRateMissing = true }
                self.broadcast("live")
            }
        }
    }

    /// watchOS relaunched the app during a workout (crash, or killed): take the running session back and keep going. The
    /// motion recorded before the relaunch is gone; heart rate and energy are in the builder.
    func recover() async {
        guard phase == .idle || phase == .sent, let s = try? await health.recoverActiveWorkoutSession() else { return }
        let b = s.associatedWorkoutBuilder()
        s.delegate = self
        b.delegate = self
        session = s
        builder = b
        startDate = s.startDate ?? Date()
        encoder = WristEncoder(rateHz: 200)
        live = LiveAnalyzer(rateHz: 200, mount: .wrist, trustedBias: true)
        swings = 0; hits = 0; hr = []
        phase = .running
        mirror(s)
        broadcast("started")
        if s.state == .running { startMotion() }
        startTicker()
    }

    /// High-rate device motion, retried a few times (the sensor can refuse right as the workout session comes up).
    fileprivate func startMotion() {
        guard motionTask == nil else { return }
        motionTask = Task { [weak self] in
            guard let self else { return }
            for attempt in 1...4 {
                do {
                    for try await batch in self.motion.deviceMotionUpdates() {
                        self.ingest(batch)
                        if self.error?.hasPrefix("Motion") == true { self.error = nil }
                    }
                    return
                } catch {
                    if Task.isCancelled || self.phase != .running { return }
                    if CMBatchedSensorManager.authorizationStatus == .denied || CMBatchedSensorManager.authorizationStatus == .restricted {
                        self.error = "Motion is off for Volante: on the watch, Settings > Privacy & Security > Motion & Fitness, turn on Fitness Tracking and Volante."
                        return
                    }
                    self.error = attempt < 4 ? "Motion is starting… (try \(attempt))" : "Motion: \(error.localizedDescription) (\((error as NSError).domain) \((error as NSError).code))"
                    try? await Task.sleep(for: .seconds(Double(attempt)))
                }
            }
        }
    }

    private func ingest(_ batch: [CMDeviceMotion]) {
        ingest(motion: batch.map { m in
            WristMotion(
                t: m.timestamp,
                userAcc: Vec3(m.userAcceleration.x, m.userAcceleration.y, m.userAcceleration.z),
                gravity: Vec3(m.gravity.x, m.gravity.y, m.gravity.z),
                rotationRate: Vec3(m.rotationRate.x, m.rotationRate.y, m.rotationRate.z)
            )
        })
    }

    /// Device-motion samples (real or simulated) -> pod-format packets + live swing analysis.
    private func ingest(motion batch: [WristMotion]) {
        var fresh: [Sample] = []
        for m in batch {
            let p = encoder.append(m)
            fresh.append(Sample(tUs: (m.t - (encoder.startTime ?? m.t)) * 1e6, v: p.v))
        }
        samples = encoder.packets.count
        for s in live.push(fresh) {
            swings = live.swingCount
            hits = live.hitCount
            lastSwingDps = s.peakDps
            bestSpeed = live.bestSpeed
        }
    }

    /// Ends the workout here (the End button, or an "end" command from the phone).
    func end() async {
        guard let session, phase == .running else { return }
        broadcast("ended")  // before end(): the mirrored channel closes with the session
        session.end()
        await finish()
    }

    /// Stops recording, saves the workout to Health and sends the session to the phone. Also runs when the phone ended the
    /// mirrored session (the delegate sees .ended while still running).
    private func finish() async {
        guard let builder, phase == .running else { return }
        phase = .ending
        lastResult = (swings, Date().timeIntervalSince(startDate))
        ticker?.cancel()
        motionTask?.cancel()
        motionTask = nil
        broadcast("ended")
        do {
            try await builder.endCollection(at: Date())
            try await builder.finishWorkout() // saved to Health as a badminton workout
        } catch {
            self.error = error.localizedDescription
        }
        send()
        self.session = nil
        self.builder = nil
    }

    // MARK: to the phone

    /// Mirrors the workout to the iPhone: Volante there is launched in the background and may start its Live Activity.
    private func mirror(_ s: HKWorkoutSession) {
        Task { @MainActor [weak self] in
            do {
                try await s.startMirroringToCompanionDevice()
                self?.mirrored = true
                self?.broadcast("started")  // the phone's mirrored session is up now: give it the numbers at once
            } catch {
                // no iPhone nearby or an older phone build: WatchConnectivity carries the session instead
            }
        }
    }

    /// "started" / "live" / "ended" with the numbers, to the phone: over the mirrored session when it is up, and over
    /// WatchConnectivity (a message when reachable; start and end also as queued user info so they arrive later).
    private func broadcast(_ event: String) {
        var msg: [String: Any] = ["event": event, "swings": swings, "hits": hits, "start": startDate.timeIntervalSince1970,
                                  "kcal": activeEnergy]
        if let heartRate { msg["bpm"] = heartRate }
        if mirrored, let s = session, let data = WorkoutWire.encode(msg) {
            s.sendToRemoteWorkoutSession(data: data) { _, _ in }
        }
        guard WCSession.default.activationState == .activated else { return }
        if WCSession.default.isReachable {
            WCSession.default.sendMessage(msg, replyHandler: nil, errorHandler: nil)
        } else if event != "live", !mirrored {
            WCSession.default.transferUserInfo(msg)
        }
    }

    /// A command from the phone ("end" when its live session ends).
    fileprivate func handle(command: String) {
        switch command {
        case "end" where phase == .running: Task { await end() }
        case "start" where phase == .idle || phase == .sent: Task { await start() }
        default: break
        }
    }

    private func send() {
        guard !encoder.packets.isEmpty, let t0 = encoder.startTime else { phase = .idle; return }
        let meta = SessionMeta(
            id: newSessionId(startDate), name: "Watch \(startDate.formatted(date: .abbreviated, time: .shortened))",
            createdAt: startDate, source: .watch, deviceName: "Apple Watch",
            // device time 0 = the first motion sample, whose CoreMotion timestamp maps to a Date through boot time
            anchor: ClockAnchor(deviceT: 0, wall: bootDate.addingTimeInterval(t0).timeIntervalSince1970),
            heartRate: hr, heartRateSource: "Apple Watch", activeEnergyKcal: activeEnergy
        )
        do {
            let url = FileManager.default.temporaryDirectory.appendingPathComponent(meta.id + ".bin")
            try Data(encoder.file()).write(to: url)
            let metaData = try JSONEncoder.osoWatch.encode(meta)
            WCSession.default.transferFile(url, metadata: [WatchTransfer.kind: "session", WatchTransfer.meta: metaData])
            phase = .sent
        } catch {
            self.error = error.localizedDescription
            phase = .idle
        }
    }
}

extension WorkoutManager: HKWorkoutSessionDelegate, HKLiveWorkoutBuilderDelegate, WCSessionDelegate {
    nonisolated func workoutSession(_ s: HKWorkoutSession, didChangeTo to: HKWorkoutSessionState, from: HKWorkoutSessionState, date: Date) {
        if to == .ended || to == .stopped {
            // ended from the phone's mirrored session (or by the system): wrap up and send like the End button
            Task { @MainActor in await self.finish() }
            return
        }
        guard to == .running else { return }
        DispatchQueue.main.async { MainActor.assumeIsolated {
            #if targetEnvironment(simulator)
            if CMBatchedSensorManager.isDeviceMotionSupported, self.phase == .running { self.startMotion() }
            #else
            if self.phase == .running { self.startMotion() }
            #endif
        } }
    }
    nonisolated func workoutSession(_ s: HKWorkoutSession, didFailWithError error: Error) {
        let message = error.localizedDescription
        DispatchQueue.main.async { MainActor.assumeIsolated { self.error = message } }
    }
    nonisolated func workoutBuilderDidCollectEvent(_ b: HKLiveWorkoutBuilder) {}

    nonisolated func workoutSession(_ s: HKWorkoutSession, didReceiveDataFromRemoteWorkoutSession data: [Data]) {
        let commands = data.compactMap { WorkoutWire.decode($0)?["command"] as? String }
        DispatchQueue.main.async { MainActor.assumeIsolated { for c in commands { self.handle(command: c) } } }
    }
    nonisolated func workoutSession(_ s: HKWorkoutSession, didDisconnectFromRemoteDeviceWithError error: Error?) {
        DispatchQueue.main.async { MainActor.assumeIsolated { self.mirrored = false } }
    }

    nonisolated func workoutBuilder(_ b: HKLiveWorkoutBuilder, didCollectDataOf types: Set<HKSampleType>) {
        for case let type as HKQuantityType in types {
            guard let stats = b.statistics(for: type) else { continue }
            switch type {
            case HKQuantityType(.heartRate):
                let unit = HKUnit.count().unitDivided(by: .minute())
                if let q = stats.mostRecentQuantity() {
                    let bpm = q.doubleValue(for: unit)
                    let at = stats.mostRecentQuantityDateInterval()?.end ?? Date()
                    // The main queue (FIFO, unlike Task hops) keeps the heart-rate samples in order
                    DispatchQueue.main.async { MainActor.assumeIsolated {
                        self.heartRate = bpm
                        self.heartRateMissing = false
                        self.hr.append(HeartRateSample(t: at.timeIntervalSince1970, bpm: bpm))
                    } }
                }
            case HKQuantityType(.activeEnergyBurned):
                let kcal = stats.sumQuantity()?.doubleValue(for: .kilocalorie())
                DispatchQueue.main.async { MainActor.assumeIsolated {
                    self.activeEnergy = kcal ?? self.activeEnergy
                } }
            default: break
            }
        }
    }

    nonisolated func session(_ s: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {}
    nonisolated func session(_ s: WCSession, didReceiveMessage message: [String: Any]) {
        guard let c = message["command"] as? String else { return }
        DispatchQueue.main.async { MainActor.assumeIsolated { self.handle(command: c) } }
    }
    nonisolated func session(_ s: WCSession, didReceiveUserInfo info: [String: Any] = [:]) {
        guard let c = info["command"] as? String else { return }
        DispatchQueue.main.async { MainActor.assumeIsolated { self.handle(command: c) } }
    }
}

extension JSONEncoder {
    static let osoWatch: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        return e
    }()
}

#if targetEnvironment(simulator)
extension WorkoutManager {
    /// Simulator only: one second of 200 Hz wrist motion per tick, with a swing every ~2.5 s (a 120 ms rotation burst of
    /// 700-1500 dps, every third one with a 6 g contact tap), and a heart rate drifting between 110 and 165 bpm.
    func startSimulatedMotion() {
        motionTask = Task { @MainActor [weak self] in
            var rng = SystemRandomNumberGenerator()
            let t0 = ProcessInfo.processInfo.systemUptime
            var nextSwing = 2.0, swingAt = -10.0, peak = 0.0, tap = false, n = 0, bpm = 112.0
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, !Task.isCancelled, self.phase == .running else { return }
                var batch: [WristMotion] = []
                for _ in 0..<200 {
                    let t = Double(n) / 200
                    n += 1
                    if t >= nextSwing {
                        swingAt = t + 0.06
                        peak = Double.random(in: 700...1500, using: &rng) * .pi / 180
                        tap = Int.random(in: 0..<3, using: &rng) == 0
                        nextSwing = t + Double.random(in: 1.8...3.2, using: &rng)
                    }
                    let bump = exp(-0.5 * pow((t - swingAt) / 0.035, 2))
                    let hit = tap && abs(t - swingAt) < 0.004 ? 6.0 : 0.0
                    let noise = { Double.random(in: -0.004...0.004, using: &rng) }
                    batch.append(WristMotion(
                        t: t0 + t,
                        userAcc: Vec3(hit + 1.5 * bump + noise(), noise(), noise()),
                        gravity: Vec3(0, 0, -1),
                        rotationRate: Vec3(0.3 * peak * bump, 0.2 * peak * bump, -peak * bump)
                    ))
                }
                self.ingest(motion: batch)
                bpm = min(165, max(110, bpm + Double.random(in: -2...4, using: &rng)))
                self.heartRate = bpm
                self.hr.append(HeartRateSample(t: Date().timeIntervalSince1970, bpm: bpm))
                self.activeEnergy += 0.15
            }
        }
    }
}
#endif
