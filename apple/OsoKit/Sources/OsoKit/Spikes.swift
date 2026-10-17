// Accel spike (impact / clap) detection. Port of app/src/core/spikes.ts (src/imu/sync.rs), made rate-aware:
// the 9-sample median window and the "narrow spike" width are in samples, the gap check scales with the sample period.

#if canImport(Darwin)
import Darwin
#endif

public let clapG = 6.0
public let impactG = 5.0
private let half = 4

/// Narrow-spike width limit in samples: 3 at 500 Hz (6 ms), never below 3.
func maxSpikeWidth(rateHz: Double) -> Int { max(3, Int((0.006 * rateHz).rounded())) }

/// Per-sample deviation of the accel components from their 9-sample median, in g; 0 next to a lost-packet gap.
public func spikeStrength(_ s: [Sample], rateHz: Double = 500) -> [Double] {
    let n = s.count
    var out = [Double](repeating: 0, count: n)
    guard n >= 2 * half + 1 else { return out }
    let maxSpan = 9.5 / rateHz // 19 ms at 500 Hz: the window must not straddle a hole
    var v = [Double](repeating: 0, count: 2 * half + 1)
    for i in half..<(n - half) {
        if s[i + half].t - s[i - half].t > maxSpan { continue }
        var d2 = 0.0
        for a in 0..<3 {
            for k in 0...(2 * half) { v[k] = s[i - half + k].acc[a] }
            v.sort()
            let dev = (s[i].acc[a] - v[half]) / G
            d2 += dev * dev
        }
        out[i] = d2.squareRoot()
    }
    return out
}

public struct Spike: Sendable {
    /// centroid device time, s
    public var t: Double
    /// peak strength, g
    public var peakG: Double
    public var index: Int
}

/// Narrow spikes above thrG at or after sample `from`.
public func spikes(_ s: [Sample], _ strength: [Double], from: Int = 0, thrG: Double, rateHz: Double = 500) -> [Spike] {
    var out: [Spike] = []
    let width = maxSpikeWidth(rateHz: rateHz)
    var i = from
    while i < s.count {
        if strength[i] <= thrG { i += 1; continue }
        let start = i
        while i < s.count && strength[i] > thrG { i += 1 }
        guard i - start <= width else { continue }
        var num = 0.0, den = 0.0, peak = 0.0, idx = start
        for k in max(0, start - 1)..<min(i + 1, s.count) {
            num += s[k].t * strength[k]; den += strength[k]
            if strength[k] > peak { peak = strength[k]; idx = k }
        }
        out.append(Spike(t: num / den, peakG: peak, index: idx))
    }
    return out
}

/// First index with t >= value.
public func lowerBound(_ s: [Sample], _ t: Double) -> Int {
    var lo = 0, hi = s.count
    while lo < hi {
        let m = (lo + hi) >> 1
        if s[m].t < t { lo = m + 1 } else { hi = m }
    }
    return lo
}

/// Sensor still in the 0.3 s before t (mean raw |gyro| < 30 dps; the bias is up to ~5 dps).
public func stillBefore(_ s: [Sample], _ t: Double) -> Bool {
    let lo = lowerBound(s, t - 0.3), hi = lowerBound(s, t - 0.02)
    guard hi > lo else { return false }
    var sum = 0.0
    for k in lo..<hi { sum += norm(s[k].gyro) }
    return sum / Double(hi - lo) < 30 * DEG
}

/// Sync claps: spikes above clapG with the sensor still just before.
public func findClaps(_ s: [Sample], _ strength: [Double], rateHz: Double = 500) -> [Spike] {
    spikes(s, strength, thrG: clapG, rateHz: rateHz).filter { stillBefore(s, $0.t) }
}
