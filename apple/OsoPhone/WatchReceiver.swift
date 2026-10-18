// Receives Apple Watch sessions (imu.bin + meta with heart rate) over WatchConnectivity and live swing counts.

import Foundation
@preconcurrency import HealthKit
import Observation
@preconcurrency import WatchConnectivity

// Main-actor state; WCSession calls its delegate on a background queue, so the callbacks are nonisolated and
// hop to the main actor with plain values (WCSession itself is not Sendable).
@MainActor
@Observable
final class WatchReceiver: NSObject, WCSessionDelegate {
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

    override init() {
        super.init()
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
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
        } }
    }

    nonisolated func session(_ s: WCSession, didReceiveMessage message: [String: Any]) {
        let swings = message["swings"] as? Int
        let bpm = message["bpm"] as? Double
        let event = message["event"] as? String
        let start = (message["start"] as? Double).map { Date(timeIntervalSince1970: $0) }
        DispatchQueue.main.async { MainActor.assumeIsolated {
            switch event {
            case "ended":
                self.workoutActive = false
                self.liveSwings = nil
                self.liveHeartRate = nil
            default:  // "started", "live", or an older watch build without events
                if !self.workoutActive { self.workoutActive = true; self.workoutStart = start ?? Date() }
                self.liveSwings = swings ?? self.liveSwings ?? 0
                self.liveHeartRate = bpm ?? self.liveHeartRate
                self.lastLive = Date()
            }
        } }
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

    /// Ends the watch workout (it then sends its session as usual).
    func endWorkout() {
        guard workoutActive, WCSession.default.activationState == .activated else { return }
        if WCSession.default.isReachable {
            WCSession.default.sendMessage(["command": "end"], replyHandler: nil)
        } else {
            WCSession.default.transferUserInfo(["command": "end"])
        }
    }

    /// No live message for a while: the watch app was killed or went out of range.
    func expireIfSilent() {
        if workoutActive, Date().timeIntervalSince(lastLive) > 30 { workoutActive = false; liveSwings = nil; liveHeartRate = nil }
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
