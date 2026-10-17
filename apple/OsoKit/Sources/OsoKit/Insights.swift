// Pure logic behind the phone's session insights: personal bests across sessions and the geometry of a swing's
// |ω| trace (phase bands, forward peak, saturated stretches). No UI; tested in OsoKitTests.

/// Best results across saved sessions. Sample sessions never count. Wrist (Apple Watch) speed is a proxy for the
/// racquet head, so it is tracked on its own and never compared with the racquet's.
public struct PersonalBests: Equatable, Sendable {
    public enum Kind: String, CaseIterable, Sendable {
        case headSpeed = "fastest head speed"
        case handSpeed = "fastest hand speed"
        case swings = "most swings"
        case rally = "longest rally"
    }

    public struct Entry: Sendable {
        public var id: String
        public var isSample: Bool
        public var mount: Mount
        public var maxSpeed: Double?
        public var swings: Int
        public var longestRally: Int
        public init(id: String, isSample: Bool, mount: Mount, maxSpeed: Double?, swings: Int, longestRally: Int) {
            self.id = id; self.isSample = isSample; self.mount = mount
            self.maxSpeed = maxSpeed; self.swings = swings; self.longestRally = longestRally
        }
    }

    public private(set) var headSpeed: Double?
    public private(set) var handSpeed: Double?
    public private(set) var mostSwings = 0
    public private(set) var longestRally = 0
    /// Sessions the bests were taken from; a personal best only means something with another session to beat.
    public private(set) var compared = 0
    private var holders: [String: [Kind]] = [:]

    public init() {}

    public init(_ entries: [Entry]) {
        let real = entries.filter { !$0.isSample }
        compared = real.count
        headSpeed = real.filter { $0.mount == .racquet }.compactMap(\.maxSpeed).max()
        handSpeed = real.filter { $0.mount == .wrist }.compactMap(\.maxSpeed).max()
        mostSwings = real.map(\.swings).max() ?? 0
        longestRally = real.map(\.longestRally).max() ?? 0
        for e in real {
            var kinds: [Kind] = []
            if let v = e.maxSpeed, v > 0 {
                if e.mount == .racquet, v == headSpeed { kinds.append(.headSpeed) }
                if e.mount == .wrist, v == handSpeed { kinds.append(.handSpeed) }
            }
            if e.swings > 0, e.swings == mostSwings { kinds.append(.swings) }
            if e.longestRally > 0, e.longestRally == longestRally { kinds.append(.rally) }
            if !kinds.isEmpty { holders[e.id] = kinds }
        }
    }

    /// What this session holds (empty with fewer than two comparable sessions).
    public func kinds(for id: String) -> [Kind] { compared >= 2 ? holders[id] ?? [] : [] }

    public func bestSpeed(for mount: Mount) -> Double? { mount == .wrist ? handSpeed : headSpeed }

    /// "Best: 21.3 m/s" context for the header, nil until there is a real session to compare with.
    public func context(for mount: Mount) -> String? {
        guard let v = bestSpeed(for: mount) else { return nil }
        let tenths = Int((v * 10).rounded())
        return "Best: \(tenths / 10).\(tenths % 10) m/s"
    }
}

/// The |ω| curve of one swing and the markers drawn on it. Times are milliseconds relative to the impact
/// (to the forward-swing peak when no impact was detected).
public struct SwingTrace: Sendable {
    public struct Point: Identifiable, Sendable { public let id: Int; public let ms: Double; public let dps: Double }
    public struct Band: Identifiable, Sendable {
        public enum Phase: String, Sendable { case backswing = "Backswing", forward = "Forward swing", follow = "Follow-through", swing = "Swing" }
        public let phase: Phase
        public let from: Double, to: Double
        public var id: String { phase.rawValue }
        public func contains(_ ms: Double) -> Bool { ms >= from && ms <= to }
    }
    public struct Clip: Identifiable, Sendable { public let id: Int; public let from: Double, to: Double }

    public var points: [Point] = []
    public var bands: [Band] = []
    public var clips: [Clip] = []
    public var impactMs: Double?
    /// Forward-swing peak: what the impact timing ("early", "on peak", "late") is measured against.
    public var fwdPeakMs = 0.0
    public var fwdPeakDps = 0.0
    /// Largest |ω| of the whole swing, often the backswing.
    public var maxMs = 0.0
    public var maxDps = 0.0
    public var yMax = 400.0
    public var xMin = 0.0, xMax = 0.0

    /// The overall max is worth its own marker only when it is a different moment from the forward peak.
    public var showsMax: Bool { abs(maxMs - fwdPeakMs) > 10 }
    public var maxInBackswing: Bool { bands.contains { $0.phase == .backswing && $0.contains(maxMs) } }
    /// The whole-swing max came from the backswing and is clearly above the forward peak.
    public var maxFromBackswing: Bool { showsMax && maxInBackswing && maxDps > fwdPeakDps * 1.02 }

    public init(_ a: SessionAnalysis, _ s: Swing, margin: Double = 0.3) {
        let t = a.capture.samples
        guard !t.isEmpty, t.count == a.wDps.count else { return }
        let origin = s.tImpact ?? s.tFwdPeak
        func ms(_ x: Double) -> Double { (x - origin) * 1000 }
        let lo = Self.lowerBound(t, s.tStart - margin), hi = Self.lowerBound(t, s.tEnd + margin)
        guard hi > lo else { return }
        points = (lo..<hi).map { Point(id: $0, ms: ms(t[$0].t), dps: a.wDps[$0]) }
        xMin = ms(s.tStart - margin)
        xMax = ms(s.tEnd + margin)
        fwdPeakMs = ms(s.tFwdPeak)
        fwdPeakDps = s.fwdPeakDps
        maxMs = ms(s.tPeak)
        maxDps = s.peakDps
        impactMs = s.tImpact.map(ms)
        yMax = max(400, (points.map(\.dps).max() ?? 0) * 1.18)

        if s.forwardMs != nil {
            let backEnd = s.tStart + (s.backswingMs ?? 0) / 1000
            let fwdEnd = s.tEnd - (s.followMs ?? 0) / 1000
            if s.backswingMs != nil, backEnd > s.tStart { bands.append(Band(phase: .backswing, from: ms(s.tStart), to: ms(backEnd))) }
            bands.append(Band(phase: .forward, from: ms(backEnd), to: ms(fwdEnd)))
            if fwdEnd < s.tEnd { bands.append(Band(phase: .follow, from: ms(fwdEnd), to: ms(s.tEnd))) }
        } else {
            bands.append(Band(phase: .swing, from: ms(s.tStart), to: ms(s.tEnd)))
        }

        var runStart: Int?
        for i in lo..<hi {
            if t[i].saturated, runStart == nil { runStart = i }
            if !t[i].saturated, let r = runStart {
                clips.append(Clip(id: r, from: ms(t[r].t), to: ms(t[i - 1].t)))
                runStart = nil
            }
        }
        if let r = runStart { clips.append(Clip(id: r, from: ms(t[r].t), to: ms(t[hi - 1].t))) }
    }

    private static func lowerBound(_ s: [Sample], _ x: Double) -> Int {
        var lo = 0, hi = s.count
        while lo < hi {
            let m = (lo + hi) >> 1
            if s[m].t < x { lo = m + 1 } else { hi = m }
        }
        return lo
    }
}
