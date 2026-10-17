// A HealthKit badminton workout that records the wrist IMU. CMBatchedSensorManager (watchOS 10+) only delivers
// high-rate motion while a workout session runs: device motion at 200 Hz in ~1 s batches. Samples become pod-format
// packets (OsoKit WristEncoder, mount 1), swings are counted live with the same detector as the phone, and at the
// end the imu.bin + heart rate go to the phone over WatchConnectivity.

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
    @ObservationIgnored private var startDate = Date()
    /// CoreMotion timestamps are seconds since boot; Date = boot + timestamp
    @ObservationIgnored private let bootDate = Date(timeIntervalSinceNow: -ProcessInfo.processInfo.systemUptime)
    @ObservationIgnored private var ticker: Task<Void, Never>?

    override init() {
        super.init()
        if WCSession.isSupported() { WCSession.default.delegate = self; WCSession.default.activate() }
    }

    func requestAuthorization() async {
        let share: Set<HKSampleType> = [HKObjectType.workoutType()]
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
        swings = 0; hits = 0; lastSwingDps = nil; heartRate = nil; activeEnergy = 0; samples = 0; hr = []
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
        } catch {
            if !simulated {
                self.error = error.localizedDescription
                phase = .idle
                return
            }
            startDate = Date() // the simulator may refuse a workout session: the demo runs without one
        }
        phase = .running
        #if targetEnvironment(simulator)
        if simulated { startSimulatedMotion() } else { startMotion() }
        #else
        startMotion()
        #endif
        // Once a second on the main actor: elapsed time and the live swing count to the phone
        ticker = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, !Task.isCancelled else { return }
                self.elapsed = Date().timeIntervalSince(self.startDate)
                self.sendLive()
            }
        }
    }

    private func startMotion() {
        motionTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await batch in self.motion.deviceMotionUpdates() {
                    self.ingest(batch)
                }
            } catch {
                self.error = "motion: \(error.localizedDescription)"
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
        }
    }

    func end() async {
        guard let session, let builder else { return }
        phase = .ending
        ticker?.cancel()
        motionTask?.cancel()
        session.end()
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

    private func sendLive() {
        guard WCSession.default.activationState == .activated, WCSession.default.isReachable else { return }
        var msg: [String: Any] = ["swings": swings]
        if let heartRate { msg["bpm"] = heartRate }
        WCSession.default.sendMessage(msg, replyHandler: nil)
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
    nonisolated func workoutSession(_ s: HKWorkoutSession, didChangeTo to: HKWorkoutSessionState, from: HKWorkoutSessionState, date: Date) {}
    nonisolated func workoutSession(_ s: HKWorkoutSession, didFailWithError error: Error) {
        let message = error.localizedDescription
        DispatchQueue.main.async { MainActor.assumeIsolated { self.error = message } }
    }
    nonisolated func workoutBuilderDidCollectEvent(_ b: HKLiveWorkoutBuilder) {}

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
