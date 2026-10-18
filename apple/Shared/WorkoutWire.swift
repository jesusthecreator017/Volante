// What the watch and the phone tell each other about a running badminton workout. The same dictionary travels two
// ways: as JSON over the mirrored HealthKit workout session (sendDataToRemoteWorkoutSession: works with the phone app in
// the background) and as a WatchConnectivity message / user info (the fallback when mirroring is not up).

import Foundation

enum WorkoutWire {
    /// watch -> phone: "event" is "started", "live" or "ended", with "swings", "hits", "bpm", "kcal", "start" (epoch s).
    /// phone -> watch: "command" is "end" or "start".
    static func encode(_ message: [String: Any]) -> Data? {
        try? JSONSerialization.data(withJSONObject: message)
    }

    static func decode(_ data: Data) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }
}
