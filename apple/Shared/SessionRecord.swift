// Saved sessions (iPhone) and the watch -> phone transfer format. Shared by both app targets.

import Foundation
import OsoKit

enum SessionSource: String, Codable {
    case podStream = "pod-stream"
    case podUpload = "pod-upload"
    case watch
    case sample
}

/// meta.json next to each session's imu.bin.
struct SessionMeta: Codable, Identifiable, Hashable {
    var id: String
    var name: String
    var createdAt: Date
    var source: SessionSource
    var deviceName: String?
    /// device clock <-> wall clock, when known (needed for heart rate)
    var anchor: ClockAnchor?
    /// sync markers from the pod (tap / 0x06), device time s
    var markers: [Double] = []
    /// heart rate from the watch workout or Fitbit
    var heartRate: [HeartRateSample] = []
    var heartRateSource: String?
    /// watch workout totals
    var activeEnergyKcal: Double?
    /// e.g. "incomplete: 900 of 1000 packets"
    var note: String?
    /// pod + watch in one session: the other sensor's session id, and how many pod swings the wrist confirmed
    var pairedSessionId: String?
    var wristConfirmed: Int?
    /// when it was sent to the Raspberry Pi server (iPhone only)
    var uploadedToPi: Date?

    static func == (a: Self, b: Self) -> Bool { a.id == b.id }
    func hash(into h: inout Hasher) { h.combine(id) }
}

/// WCSession.transferFile metadata keys (plist values only).
enum WatchTransfer {
    static let kind = "oso.kind" // "session"
    static let meta = "oso.meta" // JSON-encoded SessionMeta
}

func newSessionId(_ date: Date = Date()) -> String {
    let f = DateFormatter()
    f.dateFormat = "yyyyMMdd-HHmmss"
    f.locale = Locale(identifier: "en_US_POSIX")
    return f.string(from: date) + "-" + String(UUID().uuidString.prefix(4)).lowercased()
}
