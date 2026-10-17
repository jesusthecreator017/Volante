// Rate limit for Live Activity updates (ActivityKit budget): at most one per `minInterval`, the first one at once.
// Pure (time is passed in) so it is unit-tested.

public struct UpdateThrottle: Sendable {
    public let minInterval: Double
    public private(set) var last: Double?

    public init(minInterval: Double = 1) { self.minInterval = minInterval }

    /// Asks to send an update at `now` (seconds, any monotonic clock). Returns 0 when it may go out now (and records
    /// it), else the seconds to wait: the caller schedules one trailing update with the latest state and asks again then.
    public mutating func request(at now: Double) -> Double {
        if let last, now - last < minInterval { return max(0, last + minInterval - now) }
        last = now
        return 0
    }

    /// Forgets the history (a new activity starts: its first update is immediate).
    public mutating func reset() { last = nil }
}
