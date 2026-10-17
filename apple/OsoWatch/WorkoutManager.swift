// A HealthKit badminton workout that records the wrist IMU. CMBatchedSensorManager (watchOS 10+) only delivers
// high-rate motion while a workout session runs: device motion at 200 Hz in ~1 s batches. Samples become pod-format
// packets (OsoKit WristEncoder, mount 1), swings are counted live with the same detector as the phone, and at the
// end the imu.bin + heart rate go to the phone over WatchConnectivity.

import CoreMotion
import Foundation
import HealthKit
import Observation
import OsoKit
import WatchConnectivity

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
    @ObservationIgnored private var timer: Timer?

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

    @MainActor
    func start() async {
        guard CMBatchedSensorManager.isDeviceMotionSupported else { error = "This watch has no batched device motion (watchOS 10+, Series 8 / Ultra or later)"; return }
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
            self.error = error.localizedDescription
            phase = .idle
            return
        }
        phase = .running
        startMotion()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else { return }
            self.elapsed = Date().timeIntervalSince(self.startDate)
            self.sendLive()
        }
    }

    private func startMotion() {
        motionTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await batch in self.motion.deviceMotionUpdates() {
                    await self.ingest(batch)
                }
            } catch {
                await MainActor.run { self.error = "motion: \(error.localizedDescription)" }
            }
        }
    }

    @MainActor
    private func ingest(_ batch: [CMDeviceMotion]) {
        var fresh: [Sample] = []
        for m in batch {
            let p = encoder.append(WristMotion(
                t: m.timestamp,
                userAcc: Vec3(m.userAcceleration.x, m.userAcceleration.y, m.userAcceleration.z),
                gravity: Vec3(m.gravity.x, m.gravity.y, m.gravity.z),
                rotationRate: Vec3(m.rotationRate.x, m.rotationRate.y, m.rotationRate.z)
            ))
            fresh.append(Sample(tUs: (m.timestamp - (encoder.startTime ?? m.timestamp)) * 1e6, v: p.v))
        }
        samples = encoder.packets.count
        for s in live.push(fresh) {
            swings = live.swingCount
            hits = live.hitCount
            lastSwingDps = s.peakDps
        }
    }

    @MainActor
    func end() async {
        guard let session, let builder else { return }
        phase = .ending
        timer?.invalidate()
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

    @MainActor
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
    func workoutSession(_ s: HKWorkoutSession, didChangeTo to: HKWorkoutSessionState, from: HKWorkoutSessionState, date: Date) {}
    func workoutSession(_ s: HKWorkoutSession, didFailWithError error: Error) {
        DispatchQueue.main.async { self.error = error.localizedDescription }
    }
    func workoutBuilderDidCollectEvent(_ b: HKLiveWorkoutBuilder) {}

    func workoutBuilder(_ b: HKLiveWorkoutBuilder, didCollectDataOf types: Set<HKSampleType>) {
        for case let type as HKQuantityType in types {
            guard let stats = b.statistics(for: type) else { continue }
            DispatchQueue.main.async {
                switch type {
                case HKQuantityType(.heartRate):
                    let unit = HKUnit.count().unitDivided(by: .minute())
                    if let q = stats.mostRecentQuantity() {
                        let bpm = q.doubleValue(for: unit)
                        self.heartRate = bpm
                        let at = stats.mostRecentQuantityDateInterval()?.end ?? Date()
                        self.hr.append(HeartRateSample(t: at.timeIntervalSince1970, bpm: bpm))
                    }
                case HKQuantityType(.activeEnergyBurned):
                    self.activeEnergy = stats.sumQuantity()?.doubleValue(for: .kilocalorie()) ?? self.activeEnergy
                default: break
                }
            }
        }
    }

    func session(_ s: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {}
}

extension JSONEncoder {
    static let osoWatch: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        return e
    }()
}
