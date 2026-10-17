// DEBUG-only: -osoLiveDemo starts a Live Activity with a simulated session (no pod needed) so the Lock Screen and
// Dynamic Island can be checked on the phone. A swing every 2-3 s for 60 s, then the activity ends.

#if DEBUG
import ActivityKit
import Foundation

extension LiveActivityController {
    func runDemo() {
        Task { [weak self] in
            var s = LiveSnapshot(mode: .streaming, podName: "oso pod (demo)")
            self?.sync(s)
            try? await Task.sleep(for: .seconds(1))
            print("[oso-live] demo activities=\(Activity<LiveSessionAttributes>.activities.count) enabled=\(ActivityAuthorizationInfo().areActivitiesEnabled)")
            let end = Date().addingTimeInterval(60)
            while Date() < end {
                try? await Task.sleep(for: .seconds(Double.random(in: 2...3)))
                let speed = Double.random(in: 22...58)   // m/s head speed
                s.swings += 1
                if Bool.random() { s.hits += 1 }
                s.lastSpeed = speed
                s.bestSpeed = max(s.bestSpeed, speed)
                self?.sync(s)
            }
            s.mode = nil
            self?.sync(s)
            print("[oso-live] demo finished")
        }
    }
}
#endif
