// Receives Apple Watch sessions (imu.bin + meta with heart rate) over WatchConnectivity and the running workout's live
// numbers. A watch workout is mirrored to the phone (HealthKit workoutSessionMirroringStartHandler, set at launch so a
// background launch catches it): that channel works with the app in the background, ends the workout on both devices
// and allows the Live Activity to start from the background once. WatchConnectivity is the fallback.

import Foundation
@preconcurrency import HealthKit
import Observation
@preconcurrency import WatchConnectivity

// Main-actor state; WCSession calls its delegate on a background queue, so the callbacks are nonisolated and
// hop to the main actor with plain values (WCSession itself is not Sendable).
@MainActor
@Observable
final class WatchReceiver: NSObject, WCSessionDelegate, HKWorkoutSessionDelegate {
    private(set) var paired = false
    private(set) var installed = false
    private(set) var reachable = false
    /// live numbers sent by the watch during a workout
    private(set) var liveSwings: Int?
    private(set) var liveHeartRate: Double?
    private(set) var lastReceived: String?
    /// a badminton workout is running on the watch (from its start/live/end messages)
    private(set) var workoutActive = false
    private(set) var workoutStart: Date?
    @ObservationIgnored private var lastLive = Date.distantPast
    @ObservationIgnored private let health = HKHealthStore()
    @ObservationIgnored var onSession: ((URL, SessionMeta) -> Void)?
    /// the workout started or ended on the watch, or its live numbers changed (the app links the pod and the Live Activity)
    @ObservationIgnored var onWorkoutChange: ((_ activeChanged: Bool) -> Void)?
    /// the mirrored copy of the watch's workout session, while it runs
    @ObservationIgnored private var mirrored: HKWorkoutSession?
    @ObservationIgnored private var endedStart: Date?
    @ObservationIgnored private var askedHealth = false

    override init() {
        super.init()
        // Promptly at launch, as HealthKit asks: a watch workout that starts mirroring launches the app in the background
        health.workoutSessionMirroringStartHandler = { [weak self] session in
            nonisolated(unsafe) let session = session
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.adopt(session) } }
        }
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    /// Asks once for the HealthKit access mirroring needs (sharing workouts), when a watch with Volante is around.
    func requestWorkoutAuthorization() async {
        guard HKHealthStore.isHealthDataAvailable(), paired, installed else { return }
        try? await health.requestAuthorization(toShare: [HKObjectType.workoutType()], read: [HKQuantityType(.heartRate)])
    }

    private func adopt(_ session: HKWorkoutSession) {
        mirrored = session
        session.delegate = self
        apply(["event": "started", "start": session.startDate?.timeIntervalSince1970 ?? Date().timeIntervalSince1970])
    }

    /// One place for every watch event, whichever channel brought it.
    private func apply(_ message: [String: Any]) {
        let event = message["event"] as? String
        let start = (message["start"] as? Double).map { Date(timeIntervalSince1970: $0) }
        let wasActive = workoutActive
        switch event {
        case "ended":
            if workoutActive { endedStart = workoutStart }
            workoutActive = false
            liveSwings = nil
            liveHeartRate = nil
            mirrored = nil
        case nil where message["swings"] == nil:
            return
        default:  // "started", "live", or an older watch build without events
            if let start, let e = endedStart, abs(start.timeIntervalSince(e)) < 1 { return }  // late news of the one that ended
            if !workoutActive { workoutActive = true; workoutStart = start ?? Date() }
            liveSwings = message["swings"] as? Int ?? liveSwings ?? 0
            liveHeartRate = message["bpm"] as? Double ?? liveHeartRate
            lastLive = Date()
        }
        onWorkoutChange?(wasActive != workoutActive)
    }

    nonisolated func session(_ s: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        refresh(s)
    }
    nonisolated func sessionDidBecomeInactive(_ s: WCSession) {}
    nonisolated func sessionDidDeactivate(_ s: WCSession) { s.activate() }
    nonisolated func sessionWatchStateDidChange(_ s: WCSession) { refresh(s) }
    nonisolated func sessionReachabilityDidChange(_ s: WCSession) { refresh(s) }

    /// Reads the session's flags on the callback queue, then publishes them on the main actor.
    private nonisolated func refresh(_ s: WCSession) {
        let (p, i, r) = (s.isPaired, s.isWatchAppInstalled, s.isReachable)
        // The main queue (FIFO, unlike Task hops) keeps the updates in arrival order
        DispatchQueue.main.async { MainActor.assumeIsolated {
            self.paired = p
            self.installed = i
            self.reachable = r
            if p, i, !self.askedHealth { self.askedHealth = true; Task { await self.requestWorkoutAuthorization() } }
        } }
    }

    nonisolated func session(_ s: WCSession, didReceiveMessage message: [String: Any]) {
        nonisolated(unsafe) let message = message  // plist values only
        DispatchQueue.main.async { MainActor.assumeIsolated { self.apply(message) } }
    }
    nonisolated func session(_ s: WCSession, didReceiveUserInfo info: [String: Any] = [:]) {
        nonisolated(unsafe) let info = info
        DispatchQueue.main.async { MainActor.assumeIsolated { self.apply(info) } }
    }

    // MARK: the mirrored workout session

    nonisolated func workoutSession(_ s: HKWorkoutSession, didChangeTo to: HKWorkoutSessionState, from: HKWorkoutSessionState, date: Date) {
        guard to == .ended || to == .stopped else { return }
        DispatchQueue.main.async { MainActor.assumeIsolated { self.apply(["event": "ended"]) } }
    }
    nonisolated func workoutSession(_ s: HKWorkoutSession, didFailWithError error: Error) {}
    nonisolated func workoutSession(_ s: HKWorkoutSession, didReceiveDataFromRemoteWorkoutSession data: [Data]) {
        let messages = data.compactMap(WorkoutWire.decode)
        nonisolated(unsafe) let m = messages
        DispatchQueue.main.async { MainActor.assumeIsolated { for x in m { self.apply(x) } } }
    }
    nonisolated func workoutSession(_ s: HKWorkoutSession, didDisconnectFromRemoteDeviceWithError error: Error?) {
        // the mirror is gone (watch out of range): WatchConnectivity and the silence timeout take over
        DispatchQueue.main.async { MainActor.assumeIsolated { self.mirrored = nil } }
    }

    // MARK: driving the watch

    /// Launches Volante on the watch straight into a badminton workout (HealthKit's own iPhone-to-watch start).
    func startWorkout() async {
        guard paired, installed, !workoutActive else { return }
        let config = HKWorkoutConfiguration()
        config.activityType = .badminton
        config.locationType = .indoor
        do { try await health.startWatchApp(toHandle: config) } catch { /* the watch may be off the wrist: the pod session runs anyway */ }
    }

    /// Ends the watch workout on every channel there is (it then saves to Health and sends its session as usual).
    func endWorkout() {
        guard workoutActive else { return }
        if let m = mirrored {
            if let data = WorkoutWire.encode(["command": "end"]) { m.sendToRemoteWorkoutSession(data: data) { _, _ in } }
            m.end()  // ends the watch's primary session too
        }
        if WCSession.default.activationState == .activated {
            if WCSession.default.isReachable {
                WCSession.default.sendMessage(["command": "end"], replyHandler: nil, errorHandler: nil)
            } else {
                WCSession.default.transferUserInfo(["command": "end"])
            }
        }
    }

    /// No live message for a while (the watch app was killed or went out of range) and no mirrored session holding it.
    func expireIfSilent() {
        if workoutActive, mirrored == nil, Date().timeIntervalSince(lastLive) > 30 { apply(["event": "ended"]) }
    }

    nonisolated func session(_ s: WCSession, didReceive file: WCSessionFile) {
        // The file is deleted when this returns: move it out synchronously
        guard file.metadata?[WatchTransfer.kind] as? String == "session",
              let metaData = file.metadata?[WatchTransfer.meta] as? Data,
              let meta = try? JSONDecoder.oso.decode(SessionMeta.self, from: metaData) else { return }
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(meta.id + ".bin")
        try? FileManager.default.removeItem(at: tmp)
        do {
            try FileManager.default.moveItem(at: file.fileURL, to: tmp)
        } catch {
            return
        }
        DispatchQueue.main.async { MainActor.assumeIsolated {
            self.lastReceived = meta.name
            self.liveSwings = nil
            self.onSession?(tmp, meta)
        } }
    }
}
