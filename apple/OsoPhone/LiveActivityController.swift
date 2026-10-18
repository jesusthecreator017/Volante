// Starts, updates (at most 1 per second) and ends the pod session Live Activity. Driven by PodLink through
// `sync`; local updates only (no push token, no server). If Live Activities are off, everything here is a no-op.

@preconcurrency import ActivityKit
import Foundation
import OSLog
import OsoKit

/// What PodLink knows right now. `mode == nil` means nothing is streaming or recording.
struct LiveSnapshot: Equatable {
    var mode: LiveSessionAttributes.Mode?
    var podName: String
    var swings = 0
    var hits = 0
    var lastSpeed: Double?
    var bestSpeed = 0.0
    var connected = true
    var heartRate: Double?
}

@MainActor
final class LiveActivityController {
    /// How long a dropped link keeps the activity ("reconnecting") before it ends.
    static let reconnectGrace: Duration = .seconds(30)

    private let log = Logger(subsystem: "oso", category: "live-activity")
    private var activity: Activity<LiveSessionAttributes>?
    private var start = Date()
    private var throttle = UpdateThrottle()
    private let clock = ContinuousClock()
    private let origin = ContinuousClock.now
    private var latest: LiveState?
    private var trailing: Task<Void, Never>?
    private var graceTask: Task<Void, Never>?

    typealias LiveState = LiveSessionAttributes.ContentState

    /// Ends activities left behind by an earlier run of the app (call once at launch).
    func endStale() {
        for a in Activity<LiveSessionAttributes>.activities where a.id != activity?.id {   // never the running one
            Task { await a.end(nil, dismissalPolicy: .immediate) }
        }
    }

    /// The single entry point: reconcile the activity with the pod's state.
    func sync(_ s: LiveSnapshot) {
        guard let mode = s.mode else {
            // Nothing running. Link up: it ended normally. Link down: keep "reconnecting" for the grace period.
            if s.connected { end() } else if activity != nil { markDisconnected() }
            return
        }
        if !s.connected { armGrace() } else { graceTask?.cancel(); graceTask = nil }
        if let a = activity, a.attributes.mode != mode { end(immediately: true) }   // mode is a static attribute
        if activity == nil { guard begin(mode: mode, snapshot: s) else { return } }
        let state = LiveState(swings: s.swings, hits: s.hits, lastSpeed: s.lastSpeed, bestSpeed: s.bestSpeed, start: start, connected: s.connected, heartRate: s.heartRate)
        guard state != latest else { return }
        latest = state
        schedule()
    }

    /// Ends now with the final numbers still on the Lock Screen for a couple of minutes.
    func end(immediately: Bool = false) {
        graceTask?.cancel(); graceTask = nil
        trailing?.cancel(); trailing = nil
        guard let a = activity else { return }
        activity = nil
        var final = latest ?? a.content.state
        final.connected = true
        final.ended = Date()   // stops the timer
        latest = nil
        Task {
            await a.end(ActivityContent(state: final, staleDate: nil), dismissalPolicy: immediately ? .immediate : .after(Date().addingTimeInterval(120)))
            log.info("ended")
        }
    }

    // MARK: private

    private func begin(mode: LiveSessionAttributes.Mode, snapshot s: LiveSnapshot) -> Bool {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else {
            log.info("Live Activities are off: skipping")   // not an error
            return false
        }
        start = Date()
        throttle.reset()
        let state = LiveState(swings: s.swings, hits: s.hits, lastSpeed: s.lastSpeed, bestSpeed: s.bestSpeed, start: start, connected: s.connected, heartRate: s.heartRate)
        do {
            activity = try Activity.request(
                attributes: LiveSessionAttributes(podName: s.podName, mode: mode),
                content: ActivityContent(state: state, staleDate: nil), pushType: nil)
            latest = state
            _ = throttle.request(at: now())   // the start counts as the first update
            log.info("started")
            #if DEBUG
            print("[oso-live] started, activities=\(Activity<LiveSessionAttributes>.activities.count)")
            #endif
            return true
        } catch {
            log.error("could not start: \(error.localizedDescription)")
            return false
        }
    }

    private func now() -> Double { (clock.now - origin).seconds }

    /// Sends `latest` now, or once the 1 s window has passed (a burst collapses into one trailing update).
    private func schedule() {
        guard trailing == nil else { return }   // a trailing update already carries the newest state
        let wait = throttle.request(at: now())
        if wait == 0 { push(); return }
        trailing = Task { [weak self] in
            try? await Task.sleep(for: .seconds(wait))
            guard let self, !Task.isCancelled else { return }
            self.trailing = nil
            self.schedule()
        }
    }

    private func push() {
        guard let a = activity, let s = latest else { return }
        Task { await a.update(ActivityContent(state: s, staleDate: nil)) }
    }

    private func markDisconnected() {
        if var s = latest, s.connected { s.connected = false; latest = s; schedule() }
        armGrace()
    }

    private func armGrace() {
        guard graceTask == nil else { return }
        graceTask = Task { [weak self] in
            try? await Task.sleep(for: Self.reconnectGrace)
            guard let self, !Task.isCancelled else { return }
            self.graceTask = nil
            self.log.info("pod did not come back in 30 s")
            self.end()
        }
    }
}

private extension Duration {
    var seconds: Double { Double(components.seconds) + Double(components.attoseconds) / 1e18 }
}
