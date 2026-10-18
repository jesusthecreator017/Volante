// Swing detection (state machine on bias-removed |ω|) and per-swing metrics. Port of app/src/core/swings.ts.
// Start |ω| > 300 dps, end when < 120 dps for 80 ms, discard < 80 ms.

#if canImport(Darwin)
import Darwin
#endif

public struct SwingThresholds: Sendable {
    public var startDps = 300.0, endDps = 120.0, endHoldMs = 80.0, minDurationMs = 80.0
    public init() {}
    public init(startDps: Double, endDps: Double, endHoldMs: Double = 80, minDurationMs: Double = 80) {
        self.startDps = startDps; self.endDps = endDps; self.endHoldMs = endHoldMs; self.minDurationMs = minDurationMs
    }

    /// The wrist turns much less than the racquet head (the head adds the wrist snap on top of the forearm): light swings
    /// read 200-400 dps at the wrist, where the 300 dps racquet threshold missed most of them. Walking stays under ~120 dps.
    public static let wrist = SwingThresholds(startDps: 180, endDps: 70, endHoldMs: 80, minDurationMs: 60)
}

public struct SwingWindow: Equatable, Sendable {
    /// first sample above startDps; i1 exclusive: first sample of the quiet run that ended the swing
    public var i0, i1: Int
    public var tStart, tEnd: Double
}

/// Streaming swing state machine: push() one sample at a time; returns a window when a swing ends. Offline, live
/// (phone) and on-wrist (watch) all use it.
public struct SwingDetector: Sendable {
    public var th: SwingThresholds
    private var inSwing = false
    private var i0 = 0
    private var t0 = 0.0
    private var quiet: (i: Int, t: Double)?
    public init(_ th: SwingThresholds = SwingThresholds()) { self.th = th }

    public var active: Bool { inSwing }

    public mutating func push(_ i: Int, _ t: Double, _ wDps: Double) -> SwingWindow? {
        if !inSwing {
            if wDps > th.startDps { inSwing = true; i0 = i; t0 = t; quiet = nil }
            return nil
        }
        if wDps < th.endDps {
            if quiet == nil { quiet = (i, t) }
            if t - quiet!.t >= th.endHoldMs / 1000 { return close() }
        } else {
            quiet = nil
        }
        return nil
    }

    /// End of data: close an open swing at the start of its quiet run (or at the last sample).
    public mutating func flush(lastI: Int, lastT: Double) -> SwingWindow? {
        guard inSwing else { return nil }
        if quiet == nil { quiet = (lastI + 1, lastT) }
        return close()
    }

    private mutating func close() -> SwingWindow? {
        let q = quiet!
        inSwing = false
        quiet = nil
        let w = SwingWindow(i0: i0, i1: q.i, tStart: t0, tEnd: q.t)
        return (w.tEnd - w.tStart) * 1000 >= th.minDurationMs ? w : nil
    }
}

public func detectSwings(t: [Double], wDps: [Double], th: SwingThresholds = SwingThresholds()) -> [SwingWindow] {
    var d = SwingDetector(th)
    var out: [SwingWindow] = []
    for i in t.indices { if let w = d.push(i, t[i], wDps[i]) { out.append(w) } }
    if let last = t.last, let w = d.flush(lastI: t.count - 1, lastT: last) { out.append(w) }
    return out
}

public enum Handedness: String, Sendable { case right, left }

public struct Swing: Sendable, Identifiable {
    public var id: Int { index }
    public var index: Int
    public var tStart, tEnd: Double
    /// relative to session start, s
    public var start: Double
    public var durationMs: Double
    public var backswingMs: Double?, forwardMs: Double?, followMs: Double?
    public var peakDps: Double
    public var tPeak: Double
    public var fwdPeakDps: Double
    public var tFwdPeak: Double
    /// rotation-only head speed (racquet) or hand speed proxy (wrist), m/s
    public var headSpeed: Double
    public var peakAccG: Double
    public var impact: Bool
    public var impactG: Double?
    public var tImpact: Double?
    public var impactVsPeakMs: Double?
    public var timing: String?
    public var handleTiltDeg: Double?
    public var faceAngleDeg: Double?
    public var overhead: Bool?
    public var side: String?
    public var saturatedSamples: Int
    public var lostPackets: Int
    public var confidence: Double
    public var confidenceReasons: [String]
    /// heart rate at the swing (bpm), filled in when a heart-rate series is attached
    public var heartRate: Double?
}

public struct SwingContext {
    public var samples: [Sample]
    public var wDps: [Double]
    public var track: Track
    public var impacts: [Spike]
    public var gaps: [Gap]
    /// distance from the sensor to the point whose speed is reported (racquet head center 0.53 m)
    public var leverM: Double
    public var handedness: Handedness
    public var t0: Double
    /// tilt/face/stroke rules assume racquet axes; false for the wrist
    public var racquetFrame: Bool
}

public func analyzeSwing(_ c: SwingContext, _ w: SwingWindow, index: Int) -> Swing {
    let s = c.samples, wd = c.wDps
    var iPeak = w.i0, peakAcc = 0.0, sat = 0
    for i in w.i0..<w.i1 {
        if wd[i] > wd[iPeak] { iPeak = i }
        peakAcc = max(peakAcc, norm(s[i].acc) / G)
        if s[i].saturated { sat += 1 }
    }
    let peakDps = wd[iPeak]

    // Impact: strongest impact spike inside the window (+20 ms: contact can land right at the quiet edge)
    let imp = c.impacts.filter { $0.t >= w.tStart && $0.t <= w.tEnd + 0.02 }.max { $0.peakG < $1.peakG }

    // Forward-swing peak: largest |ω| in the 150 ms before the impact (global peak without one)
    var iFwd = iPeak
    if let imp {
        let lo = max(w.i0, lowerBound(s, imp.t - 0.15)), hi = min(w.i1, lowerBound(s, imp.t + 0.005))
        if hi > lo {
            iFwd = lo
            for i in lo..<hi where wd[i] > wd[iFwd] { iFwd = i }
        }
    }
    let tFwd = s[iFwd].t

    // Backswing/forward reversal: the |ω| valley before the forward lobe
    var iValley: Int?
    var j = iFwd
    while j > w.i0 && wd[j - 1] >= 0.6 * wd[iFwd] { j -= 1 }
    var iMax = w.i0
    for i in w.i0..<max(w.i0, j) where wd[i] > wd[iMax] { iMax = i }
    if j > w.i0 && iMax < iFwd {
        var iv = iMax
        for i in iMax..<iFwd where wd[i] < wd[iv] { iv = i }
        if wd[iv] < 0.6 * min(wd[iMax], wd[iFwd]) { iValley = iv }
    }
    let split2 = imp?.t ?? tFwd
    let backswingMs = iValley.map { (s[$0].t - w.tStart) * 1000 }
    let forwardMs = (split2 - (iValley.map { s[$0].t } ?? w.tStart)) * 1000

    // Orientation at impact (or forward peak)
    let tq = imp?.t ?? tFwd
    let at = trackAt(c.track, tq)
    var tilt: Double?, face: Double?, overhead: Bool?
    if c.racquetFrame, let q = at?.q ?? nearestQ(c.track, tq) {
        tilt = asin(clamp1(q.rotate(Vec3(0, 1, 0)).y)) / DEG
        face = asin(clamp1(q.rotate(Vec3(0, 0, 1)).y)) / DEG
        overhead = tilt! > 0
    }
    // Forehand/backhand: sign of the rate about the face normal (+Z) at the forward peak; right hand: + = forehand.
    var side: String?
    if c.racquetFrame {
        let wz = c.track.w[iFwd].z / DEG * (c.handedness == .right ? 1 : -1)
        side = abs(wz) < 50 ? nil : wz > 0 ? "forehand" : "backhand"
    }

    let headSpeed = peakDps * DEG * c.leverM
    var impactVsPeakMs: Double?, timing: String?
    if let imp {
        let d = (imp.t - tFwd) * 1000
        impactVsPeakMs = d
        timing = d < -10 ? "early" : d > 10 ? "late" : "on peak"
    }

    var reasons: [String] = []
    var conf = 1.0, lost = 0, bigNearKey = false
    for g in c.gaps where !(g.tEnd < w.tStart - 0.02 || g.tStart > w.tEnd) {
        lost += g.missing
        let keys = [tFwd] + (imp.map { [$0.t] } ?? [])
        if g.missing >= 4 && keys.contains(where: { $0 >= g.tStart - 0.03 && $0 <= g.tEnd + 0.03 }) { bigNearKey = true }
    }
    if lost > 0 { conf -= min(0.4, Double(lost) * 0.02); reasons.append("\(lost) packets lost inside the swing") }
    if bigNearKey { conf -= 0.2; reasons.append("a gap of 4+ packets near the peak/impact (impact may be missed)") }
    if sat > 0 { conf -= 0.2; reasons.append("gyro saturated on \(sat) samples (peak |ω| and speed are lower bounds)") }
    if let at, at.conf < 1 { conf -= 0.1; reasons.append("orientation low-confidence at impact") }

    return Swing(
        index: index, tStart: w.tStart, tEnd: w.tEnd, start: w.tStart - c.t0, durationMs: (w.tEnd - w.tStart) * 1000,
        backswingMs: backswingMs, forwardMs: forwardMs, followMs: (w.tEnd - split2) * 1000,
        peakDps: peakDps, tPeak: s[iPeak].t, fwdPeakDps: wd[iFwd], tFwdPeak: tFwd, headSpeed: headSpeed,
        peakAccG: peakAcc, impact: imp != nil, impactG: imp?.peakG, tImpact: imp?.t, impactVsPeakMs: impactVsPeakMs,
        timing: timing, handleTiltDeg: tilt, faceAngleDeg: face, overhead: overhead, side: side,
        saturatedSamples: sat, lostPackets: lost, confidence: max(0.1, min(1, conf)), confidenceReasons: reasons
    )
}

private func nearestQ(_ tr: Track, _ t: Double) -> Quat? {
    guard !tr.t.isEmpty else { return nil }
    var lo = 0, hi = tr.t.count - 1
    while lo < hi {
        let m = (lo + hi) >> 1
        if tr.t[m] < t { lo = m + 1 } else { hi = m }
    }
    return tr.q[lo]
}
