// Receives Apple Watch sessions (imu.bin + meta with heart rate) over WatchConnectivity and live swing counts.

import Foundation
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
        DispatchQueue.main.async { MainActor.assumeIsolated {
            self.liveSwings = swings
            self.liveHeartRate = bpm
        } }
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
