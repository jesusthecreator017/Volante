// Session analysis (offline, saved sessions) and the live analyzer (streaming on the phone, on-wrist on the watch).
// Offline follows app/src/core/analysis.ts; the summary keeps the numbers the apps show.

public struct AnalysisSettings: Sendable {
    public var beta = defaultBeta
    public var thresholds = SwingThresholds()
    public var handedness = Handedness.right
    /// racquet head center from the butt
    public var racquetLeverM = 0.53
    /// wrist: forearm length proxy for a hand-speed estimate
    public var wristLeverM = 0.30
    /// a rally ends when hits are this far apart
    public var rallyGapS = 4.0
    public init() {}
}

public struct SessionSummary: Sendable {
    public var durationS: Double
    public var activeS: Double
    public var swingCount: Int
    public var hitCount: Int
    public var hitsPerMin: Double
    public var peakDps: Double?
    public var maxSpeed: Double?
    public var meanSpeed: Double?
    public var rallyCount: Int
    public var longestRally: Int
    public var lossPct: Double
    public var saturatedGyro: Int
}

public struct SessionAnalysis: Sendable {
    public var capture: Capture
    public var mount: Mount
    public var t0: Double, t1: Double
    public var fused: Fused?
    public var wDps: [Double]
    public var impacts: [Spike]
    public var claps: [Spike]
    public var swings: [Swing]
    public var summary: SessionSummary
}

public func analyzeSession(_ cap: Capture, settings: AnalysisSettings = AnalysisSettings()) -> SessionAnalysis {
    let s = cap.samples
    let rate = cap.rateHz > 0 ? cap.rateHz : 500
    let mount = cap.header.mount ?? .racquet
    let t0 = s.first?.t ?? 0, t1 = s.last?.t ?? 0
    let fused = s.isEmpty ? nil : calibrateAndFuse(
        s, params: FusionParams(beta: settings.beta, twoPass: true, nominalDt: 1 / rate), rateHz: rate,
        assumeZeroBias: mount == .wrist
    )
    let bias = fused?.bias ?? .zero
    let wDps = s.map { norm($0.gyro - bias) / DEG }
    let strength = spikeStrength(s, rateHz: rate)
    let all = spikes(s, strength, thrG: impactG, rateHz: rate)
    let claps = findClaps(s, strength, rateHz: rate)
    let clapTimes = Set(claps.map(\.t))
    let impacts = all.filter { !clapTimes.contains($0.t) }
    var swings: [Swing] = []
    if let fused {
        let ctx = SwingContext(
            samples: s, wDps: wDps, track: fused.track, impacts: impacts, gaps: cap.gaps,
            leverM: mount == .wrist ? settings.wristLeverM : settings.racquetLeverM,
            handedness: settings.handedness, t0: t0, racquetFrame: mount == .racquet
        )
        swings = detectSwings(t: fused.track.t, wDps: wDps, th: settings.thresholds).enumerated().map {
            analyzeSwing(ctx, $0.element, index: $0.offset + 1)
        }
    }
    return SessionAnalysis(
        capture: cap, mount: mount, t0: t0, t1: t1, fused: fused, wDps: wDps, impacts: impacts, claps: claps,
        swings: swings, summary: summarize(swings, cap: cap, wDps: wDps, t0: t0, t1: t1, settings: settings)
    )
}

func summarize(_ swings: [Swing], cap: Capture, wDps: [Double], t0: Double, t1: Double, settings: AnalysisSettings) -> SessionSummary {
    let duration = max(0, t1 - t0)
    // Active time: union of swing windows padded by 0.5 s each side
    var active = 0.0
    var cur: (Double, Double)?
    for (a, b) in swings.map({ (max(t0, $0.tStart - 0.5), min(t1, $0.tEnd + 0.5)) }).sorted(by: { $0.0 < $1.0 }) {
        if let c = cur, a <= c.1 { cur = (c.0, max(c.1, b)) } else {
            if let c = cur { active += c.1 - c.0 }
            cur = (a, b)
        }
    }
    if let c = cur { active += c.1 - c.0 }
    let hits = swings.filter(\.impact)
    var rallies: [Int] = []
    var last: Double?
    for h in hits {
        let t = h.tImpact ?? h.tStart
        if let l = last, t - l <= settings.rallyGapS { rallies[rallies.count - 1] += 1 } else { rallies.append(1) }
        last = t
    }
    let speeds = swings.map(\.headSpeed)
    let st = cap.stats
    return SessionSummary(
        durationS: duration, activeS: active, swingCount: swings.count, hitCount: hits.count,
        hitsPerMin: duration > 0 ? Double(hits.count) / (duration / 60) : 0,
        peakDps: wDps.max(), maxSpeed: speeds.max(),
        meanSpeed: speeds.isEmpty ? nil : speeds.reduce(0, +) / Double(speeds.count),
        rallyCount: rallies.count, longestRally: rallies.max() ?? 0,
        lossPct: st.unique + st.lost > 0 ? 100 * Double(st.lost) / Double(st.unique + st.lost) : 0,
        saturatedGyro: cap.samples.filter(\.saturated).count
    )
}

/// Live analysis, one sample at a time: bias calibration from the first still second, then swings on |ω| and
/// impact spikes from a short accel window. Cheap enough for the watch.
public struct LiveAnalyzer: Sendable {
    public let rateHz: Double
    public var settings: AnalysisSettings
    public private(set) var bias: Vec3?
    public private(set) var swingCount = 0
    public private(set) var hitCount = 0
    public private(set) var lastSwingPeakDps: Double?
    public private(set) var bestSpeed: Double?
    public private(set) var currentDps = 0.0
    private let lever: Double
    private var detector: SwingDetector
    private var calib: [Sample] = []
    private var stillBlocks = 0
    private var window: [Sample] = []
    private var index = 0
    private var swingPeak = 0.0
    private var swingSawImpact = false

    /// `trustedBias`: the source corrects its own gyro bias (Apple device motion): skip calibration.
    public init(rateHz: Double, mount: Mount, settings: AnalysisSettings = AnalysisSettings(), trustedBias: Bool = false) {
        self.rateHz = rateHz
        self.settings = settings
        detector = SwingDetector(settings.thresholds)
        lever = mount == .wrist ? settings.wristLeverM : settings.racquetLeverM
        if trustedBias { bias = .zero }
    }

    public var calibrated: Bool { bias != nil }

    /// Feeds samples in time order; returns the swings that ended.
    public mutating func push(_ samples: [Sample]) -> [Swing] {
        var ended: [Swing] = []
        for s in samples {
            if bias == nil { calibrate(s); continue }
            let w = norm(s.gyro - bias!) / DEG
            currentDps = w
            // 9-sample window centred 4 back: impact strength of the sample in the middle
            window.append(s)
            if window.count > 9 { window.removeFirst() }
            if window.count == 9, detector.active, spikeStrength(window, rateHz: rateHz)[4] > impactG { swingSawImpact = true }
            if detector.active { swingPeak = max(swingPeak, w) }
            if let sw = detector.push(index, s.t, w) {
                swingCount += 1
                if swingSawImpact { hitCount += 1 }
                let speed = swingPeak * DEG * lever
                lastSwingPeakDps = swingPeak
                bestSpeed = max(bestSpeed ?? 0, speed)
                ended.append(Swing(
                    index: swingCount, tStart: sw.tStart, tEnd: sw.tEnd, start: sw.tStart, durationMs: (sw.tEnd - sw.tStart) * 1000,
                    peakDps: swingPeak, tPeak: sw.tStart, fwdPeakDps: swingPeak, tFwdPeak: sw.tStart, headSpeed: speed,
                    peakAccG: 0, impact: swingSawImpact,
                    saturatedSamples: 0, lostPackets: 0, confidence: 1, confidenceReasons: []
                ))
            }
            if !detector.active { swingPeak = 0; swingSawImpact = false }
            index += 1
        }
        return ended
    }

    private mutating func calibrate(_ s: Sample) {
        calib.append(s)
        let block = blockSize(rateHz: rateHz)
        guard calib.count % block == 0 else { return }
        if blockIsStill(calib, calib.count - block, calib.count) {
            stillBlocks += 1
            if stillBlocks >= 10 { bias = gyroBias(calib, SampleWindow(start: 0, end: calib.count - block)); calib = [] }
        } else {
            stillBlocks = 0
            calib = []
        }
    }
}

extension Swing {
    /// Memberwise init with the optional fields defaulted (live swings).
    init(index: Int, tStart: Double, tEnd: Double, start: Double, durationMs: Double, peakDps: Double, tPeak: Double,
         fwdPeakDps: Double, tFwdPeak: Double, headSpeed: Double, peakAccG: Double, impact: Bool,
         saturatedSamples: Int, lostPackets: Int, confidence: Double, confidenceReasons: [String]) {
        self.init(index: index, tStart: tStart, tEnd: tEnd, start: start, durationMs: durationMs, backswingMs: nil,
                  forwardMs: nil, followMs: nil, peakDps: peakDps, tPeak: tPeak, fwdPeakDps: fwdPeakDps, tFwdPeak: tFwdPeak,
                  headSpeed: headSpeed, peakAccG: peakAccG, impact: impact, impactG: nil, tImpact: nil, impactVsPeakMs: nil,
                  timing: nil, handleTiltDeg: nil, faceAngleDeg: nil, overhead: nil, side: nil,
                  saturatedSamples: saturatedSamples, lostPackets: lostPackets, confidence: confidence,
                  confidenceReasons: confidenceReasons, heartRate: nil)
    }
}
