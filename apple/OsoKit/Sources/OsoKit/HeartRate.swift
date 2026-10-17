// Heart rate next to the motion data: samples from the watch workout (HealthKit) or Fitbit (Google Health API),
// put on the session's device clock through a clock anchor, then attached to swings and rallies.

/// Wall-clock time as seconds since 1970 (what Date().timeIntervalSince1970 gives), kept Foundation-free here.
public typealias EpochSeconds = Double

public struct HeartRateSample: Sendable, Equatable, Codable {
    public var t: EpochSeconds
    public var bpm: Double
    public init(t: EpochSeconds, bpm: Double) { self.t = t; self.bpm = bpm }
}

/// Maps the session's device clock to wall-clock time: wall = wallAtAnchor + (deviceT - deviceAtAnchor).
/// Pod: the phone sends a sync marker right after connecting and pairs the marker's t_us with the phone time it
/// arrived. Watch: CoreMotion timestamps map to Date directly. Drift between the clocks (~40 ppm) is ignored: < 0.1 s/h.
public struct ClockAnchor: Sendable, Equatable, Codable {
    public var deviceT: Double
    public var wall: EpochSeconds
    public init(deviceT: Double, wall: EpochSeconds) { self.deviceT = deviceT; self.wall = wall }
    public func wall(_ deviceT: Double) -> EpochSeconds { wall + (deviceT - self.deviceT) }
    public func device(_ wall: EpochSeconds) -> Double { deviceT + (wall - self.wall) }
}

public struct HeartRateSeries: Sendable {
    public private(set) var samples: [HeartRateSample]
    public init(_ samples: [HeartRateSample]) { self.samples = samples.sorted { $0.t < $1.t } }

    /// Linear interpolation between neighbours no more than `maxGap` apart; nil outside the data.
    public func bpm(at t: EpochSeconds, maxGap: Double = 10) -> Double? {
        guard let first = samples.first, let last = samples.last, t >= first.t - 1, t <= last.t + 1 else { return nil }
        var lo = 0, hi = samples.count
        while lo < hi {
            let m = (lo + hi) >> 1
            if samples[m].t < t { lo = m + 1 } else { hi = m }
        }
        if lo == 0 { return first.bpm }
        if lo == samples.count { return last.bpm }
        let a = samples[lo - 1], b = samples[lo]
        guard b.t - a.t <= maxGap else { return nil }
        return a.bpm + (b.bpm - a.bpm) * (t - a.t) / max(1e-9, b.t - a.t)
    }

    public func stats(from: EpochSeconds, to: EpochSeconds) -> (mean: Double, max: Double, min: Double)? {
        let xs = samples.filter { $0.t >= from && $0.t <= to }.map(\.bpm)
        guard let mx = xs.max(), let mn = xs.min() else { return nil }
        return (xs.reduce(0, +) / Double(xs.count), mx, mn)
    }
}

/// Fills `heartRate` on each swing (at its start).
public func attachHeartRate(_ swings: [Swing], _ hr: HeartRateSeries, anchor: ClockAnchor) -> [Swing] {
    swings.map { var s = $0; s.heartRate = hr.bpm(at: anchor.wall(s.tStart)); return s }
}
