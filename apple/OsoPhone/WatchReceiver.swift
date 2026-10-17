// Receives Apple Watch sessions (imu.bin + meta with heart rate) over WatchConnectivity and live swing counts.

import Foundation
import Observation
import WatchConnectivity

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

    func session(_ s: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        DispatchQueue.main.async { self.refresh(s) }
    }
    func sessionDidBecomeInactive(_ s: WCSession) {}
    func sessionDidDeactivate(_ s: WCSession) { s.activate() }
    func sessionWatchStateDidChange(_ s: WCSession) { DispatchQueue.main.async { self.refresh(s) } }
    func sessionReachabilityDidChange(_ s: WCSession) { DispatchQueue.main.async { self.refresh(s) } }

    private func refresh(_ s: WCSession) {
        paired = s.isPaired
        installed = s.isWatchAppInstalled
        reachable = s.isReachable
    }

    func session(_ s: WCSession, didReceiveMessage message: [String: Any]) {
        DispatchQueue.main.async {
            self.liveSwings = message["swings"] as? Int
            self.liveHeartRate = message["bpm"] as? Double
        }
    }

    func session(_ s: WCSession, didReceive file: WCSessionFile) {
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
        DispatchQueue.main.async {
            self.lastReceived = meta.name
            self.liveSwings = nil
            self.onSession?(tmp, meta)
        }
    }
}
