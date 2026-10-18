// Session report `volante-report/1` (docs/strokes/report-format.md) and its insights: the phone's port of tools/strokes/report.py
// (same swings and speeds as the style profile) plus a deterministic rule engine. Every insight cites the numbers it used, never
// gives medical advice, never claims what the sensor cannot see, and never fires on fewer than 30 swings.

import Foundation

public struct SessionReport: Equatable, Sendable {
    public struct Minute: Equatable, Sendable { public var tMin: Int, swings: Int; public var p90Kmh: Double?, hrBpm: Double? }
    public struct Strokes: Equatable, Sendable {
        /// power / mid / touch / unclassified (the recommended 4-group model)
        public var groups: [String: Int]
        public var experimental = true
        public init(groups: [String: Int], experimental: Bool = true) { self.groups = groups; self.experimental = experimental }
    }
    public struct Insight: Equatable, Sendable, Identifiable {
        public enum Severity: String, Sendable { case info, tip, praise }
        public var id: String
        public var severity: Severity
        public var title: String
        public var body: String
        /// the numbers the rule used, in the order it used them
        public var evidence: [(String, Double)]
        public static func == (a: Insight, b: Insight) -> Bool {
            a.id == b.id && a.severity == b.severity && a.title == b.title && a.body == b.body && a.evidence.map(\.0) == b.evidence.map(\.0) && a.evidence.map(\.1) == b.evidence.map(\.1)
        }
    }

    public var startedAt: String
    public var durationS: Double, activeS: Double
    public var source: String, podFw: String
    public var swings: Int
    public var perMinActive: Double?
    public var p50Kmh: Double?, p90Kmh: Double?, maxKmh: Double?
    public var clippedFrac: Double
    public var medianGapS: Double?, p25GapS: Double?, p75GapS: Double?
    public var rallies: Int
    public var medianShots: Double?
    public var longestShots: Int
    public var timeline: [Minute]
    public var avgBpm: Double?, maxBpm: Double?
    public var strokes: Strokes?
    public var insights: [Insight] = []

    /// Not in the file: what the rules need (cv of the power swings, p90 in the first and last third of the session).
    public var powerCV: Double?
    public var firstThirdP90: Double?, lastThirdP90: Double?

    // MARK: JSON (the layout of Python's json.dumps(indent=2), so the parity vector compares byte for byte)

    public func json() -> String {
        func n(_ x: Double?) -> J { x.map { .num($0) } ?? .null }
        let insightsJ: J = .arr(insights.map { i in
            .obj([("id", .str(i.id)), ("severity", .str(i.severity.rawValue)), ("title", .str(i.title)), ("body", .str(i.body)),
                  ("evidence", .obj(i.evidence.map { ($0.0, .num($0.1)) }))])
        })
        let root: J = .obj([
            ("schema", .str("volante-report/1")),
            ("session", .obj([("started_at", .str(startedAt)), ("duration_s", .num(durationS)), ("active_s", .num(activeS)),
                              ("source", .str(source)), ("pod_fw", .str(podFw))])),
            ("swings", .obj([("count", .int(swings)), ("per_min_active", n(perMinActive))])),
            ("speed", .obj([("p50_kmh", n(p50Kmh)), ("p90_kmh", n(p90Kmh)), ("max_kmh", n(maxKmh)),
                            ("clipped_frac", swings == 0 ? .int(0) : .num(clippedFrac)), ("estimated", .bool(true))])),
            ("tempo", .obj([("median_gap_s", n(medianGapS)), ("p25_gap_s", n(p25GapS)), ("p75_gap_s", n(p75GapS))])),
            ("rallies", .obj([("count", .int(rallies)), ("median_shots", n(medianShots)), ("longest_shots", .int(longestShots))])),
            ("timeline", .arr(timeline.map { .obj([("t_min", .int($0.tMin)), ("swings", .int($0.swings)), ("p90_kmh", n($0.p90Kmh)), ("hr_bpm", n($0.hrBpm))]) })),
            ("heart_rate", avgBpm.map { .obj([("avg_bpm", .num($0)), ("max_bpm", n(maxBpm))]) } ?? .null),
            ("strokes", strokes.map { s in .obj([("groups", .obj(s.groups.sorted { $0.key < $1.key }.map { ($0.key, .int($0.value)) })), ("experimental", .bool(s.experimental))]) } ?? .null),
            ("insights", insightsJ),
            ("model", strokes == nil ? .null : .obj([("name", .str("stroke_group_v0")), ("version", .str("0"))])),
        ])
        return root.pretty("") + "\n"
    }
}

/// Ordered JSON, printed like Python's json.dumps(indent=2).
indirect enum J {
    case num(Double), int(Int), str(String), bool(Bool), null, arr([J]), obj([(String, J)])

    func pretty(_ ind: String) -> String {
        switch self {
        case .num(let x): return pyFloat(x)
        case .int(let i): return "\(i)"
        case .bool(let b): return b ? "true" : "false"
        case .null: return "null"
        case .str(let s): return jsonString(s)
        case .arr(let xs):
            if xs.isEmpty { return "[]" }
            let next = ind + "  "
            return "[\n" + xs.map { next + $0.pretty(next) }.joined(separator: ",\n") + "\n\(ind)]"
        case .obj(let kv):
            if kv.isEmpty { return "{}" }
            let next = ind + "  "
            return "{\n" + kv.map { next + jsonString($0.0) + ": " + $0.1.pretty(next) }.joined(separator: ",\n") + "\n\(ind)}"
        }
    }
}

func jsonString(_ s: String) -> String {
    var o = "\""
    for u in s.unicodeScalars {
        switch u {
        case "\"": o += "\\\""
        case "\\": o += "\\\\"
        case "\n": o += "\\n"
        case "\t": o += "\\t"
        default:
            if u.value < 0x20 || u.value > 0x7e { o += String(format: "\\u%04x", u.value) } else { o.unicodeScalars.append(u) }
        }
    }
    return o + "\""
}

/// Python's float repr (shortest round trip; 2.0 keeps its ".0"; 1e-05 style exponents).
func pyFloat(_ x: Double) -> String {
    if x == x.rounded(), abs(x) < 1e16 { return String(format: "%.1f", x) }
    return "\(x)"
}

public enum SessionReporter {
    public static let rallySplitS = 6.0
    public static let windowS = StyleProfile.windowS

    /// The report of one session's recording parts. `hr`: heart rate on the first part's device clock (t, bpm).
    public static func build(parts: [[Sample]], startedAt: String, source: String = "pod", podFw: String = "unknown",
                             mount: StyleMount = .flat, hr: [(t: Double, bpm: Double)] = [], strokes: SessionReport.Strokes? = nil) -> SessionReport {
        var sw: [ProfileSwing] = [], gaps: [Double] = [], runs: [[ProfileSwing]] = [], duration = 0.0
        for part in parts {
            if let a = part.first, let b = part.last { duration += b.t - a.t }
            let s = StyleProfiler.swings(part, mount: mount)
            var run: [ProfileSwing] = s.first.map { [$0] } ?? []
            if s.count > 1 {
                for k in 1..<s.count {
                    let g = s[k].t - s[k - 1].t
                    if g <= rallySplitS { gaps.append(g); run.append(s[k]) } else { runs.append(run); run = [s[k]] }
                }
            }
            if !run.isEmpty { runs.append(run) }
            sw += s
        }
        let active = runs.reduce(0.0) { $0 + ($1.last!.t - $1.first!.t) + windowS }
        let real = runs.map(\.count).filter { $0 >= 2 }
        let sp = sw.map(\.kmh).sorted(), gs = gaps.sorted()
        let t0 = parts.first?.first?.t ?? 0
        var timeline: [SessionReport.Minute] = []
        if let first = parts.first, let last = first.last {
            let nMin = Int(((last.t - t0) / 60).rounded(.down)) + 1
            for m in 0..<nMin {
                let lo = t0 + 60 * Double(m), hi = lo + 60
                let inside = sw.filter { $0.t >= lo && $0.t < hi }.map(\.kmh).sorted()
                let hrs = hr.filter { $0.t >= lo && $0.t < hi }.map(\.bpm)
                timeline.append(.init(tMin: m, swings: inside.count, p90Kmh: inside.count >= 3 ? sig3(percentile(inside, 0.9)) : nil,
                                      hrBpm: hrs.isEmpty ? nil : sig3(hrs.reduce(0, +) / Double(hrs.count))))
            }
        }
        var r = SessionReport(
            startedAt: startedAt, durationS: sig3(duration), activeS: sig3(active), source: source, podFw: podFw,
            swings: sw.count, perMinActive: active > 0 ? sig3(Double(sw.count) / (active / 60)) : nil,
            p50Kmh: sp.isEmpty ? nil : sig3(percentile(sp, 0.5)), p90Kmh: sp.isEmpty ? nil : sig3(percentile(sp, 0.9)),
            maxKmh: sp.last.map(sig3), clippedFrac: sw.isEmpty ? 0 : sig3(Double(sw.filter(\.clipped).count) / Double(sw.count)),
            medianGapS: gs.isEmpty ? nil : sig3(percentile(gs, 0.5)), p25GapS: gs.isEmpty ? nil : sig3(percentile(gs, 0.25)),
            p75GapS: gs.isEmpty ? nil : sig3(percentile(gs, 0.75)),
            rallies: real.count, medianShots: real.isEmpty ? nil : sig3(median(real.map(Double.init))), longestShots: real.max() ?? 0,
            timeline: timeline,
            avgBpm: hr.isEmpty ? nil : sig3(hr.map(\.bpm).reduce(0, +) / Double(hr.count)), maxBpm: hr.map(\.bpm).max().map(sig3),
            strokes: strokes)
        // rule inputs
        if !sp.isEmpty {
            let p50 = percentile(sp, 0.5)
            let power = sp.filter { $0 >= p50 }
            let mean = power.reduce(0, +) / Double(power.count)
            let sd = (power.map { ($0 - mean) * ($0 - mean) }.reduce(0, +) / Double(power.count)).squareRoot()
            r.powerCV = mean > 0 ? sd / mean : nil
        }
        let third = sw.count / 3
        if third >= 10 {
            r.firstThirdP90 = percentile(sw.prefix(third).map(\.kmh).sorted(), 0.9)
            r.lastThirdP90 = percentile(sw.suffix(third).map(\.kmh).sorted(), 0.9)
        }
        return r
    }

    static func median(_ v: [Double]) -> Double { percentile(v.sorted(), 0.5) }
}

// MARK: insights

/// What the rules compare against: the player's earlier real sessions (not samples), newest first, and this week's goal.
public struct InsightContext: Sendable {
    public var history: [SessionReport]
    public var weeklyGoal: (value: Double, target: Double, unit: String)?
    public init(history: [SessionReport] = [], weeklyGoal: (value: Double, target: Double, unit: String)? = nil) {
        self.history = history; self.weeklyGoal = weeklyGoal
    }
}

public enum InsightRules {
    // thresholds (the contract for the copy below; change both together)
    public static let minSwings = 30
    public static let speedFadePct = 10.0
    public static let nearBestPct = 3.0
    public static let tempoChangePct = 15.0
    public static let tempoHistory = 5
    public static let shortRallyShots = 4.0
    public static let longRallyShots = 10.0
    public static let steadyCV = 0.10
    public static let wildCV = 0.20
    public static let clippedFrac = 0.20
    public static let lowTouchShare = 0.10
    public static let maxInsights = 4

    private struct Candidate { var family: String; var priority: Double; var insight: SessionReport.Insight }

    /// The top insights for `r` (2-4 when there is enough to say; one "play longer" note below `minSwings`).
    public static func insights(for r: SessionReport, _ ctx: InsightContext = .init()) -> [SessionReport.Insight] {
        typealias I = SessionReport.Insight
        guard r.swings >= minSwings else {
            return [I(id: "play_longer", severity: .info, title: "Play a bit longer for insights",
                      body: "Insights need at least \(minSwings) swings; this session has \(r.swings).", evidence: [("swings", Double(r.swings))])]
        }
        var c: [Candidate] = []
        func add(_ family: String, _ p: Double, _ id: String, _ sev: I.Severity, _ title: String, _ body: String, _ ev: [(String, Double)]) {
            c.append(.init(family: family, priority: p, insight: I(id: id, severity: sev, title: title, body: body, evidence: ev)))
        }
        let f1 = { (x: Double) in String(format: "%.1f", x) }
        let f0 = { (x: Double) in String(format: "%.0f", x) }

        // speed: a fade over the session, or a (near) personal best
        if let a = r.firstThirdP90, let b = r.lastThirdP90, a > 0 {
            let drop = (a - b) / a * 100
            if drop >= speedFadePct {
                add("speed", 70 + drop, "speed_fade", .tip, "Your hardest swings faded",
                    "Hard swings (p90) went from \(f0(a)) to \(f0(b)) km/h between the first and last third, \(f0(drop)) % slower. A rest or water break can help keep power late.",
                    [("first_third_p90_kmh", sig3(a)), ("last_third_p90_kmh", sig3(b)), ("drop_pct", sig3(drop))])
            }
        }
        let pastMax = ctx.history.compactMap(\.maxKmh).max()
        if let m = r.maxKmh, let best = pastMax, r.clippedFrac <= clippedFrac {
            if m > best {
                add("speed", 90, "new_best", .praise, "New fastest swing", "\(f0(m)) km/h beats your previous best of \(f0(best)) km/h (estimated).",
                    [("max_kmh", m), ("previous_best_kmh", best)])
            } else if (best - m) / best * 100 <= nearBestPct {
                add("speed", 60, "near_best", .praise, "Close to your best", "\(f0(m)) km/h is within \(f0(nearBestPct)) % of your best, \(f0(best)) km/h.",
                    [("max_kmh", m), ("best_kmh", best)])
            }
        }
        // tempo vs the last sessions
        let usual = ctx.history.prefix(tempoHistory).compactMap(\.medianGapS)
        if let g = r.medianGapS, usual.count >= 3 {
            let u = SessionReporter.median(usual), d = (g - u) / u * 100
            if abs(d) >= tempoChangePct {
                add("tempo", 50 + abs(d), "tempo_vs_usual", .info, d < 0 ? "Quicker than usual" : "Slower than usual",
                    "Your median time between shots was \(f1(g)) s, \(f0(abs(d))) % \(d < 0 ? "quicker" : "slower") than your last \(usual.count) sessions (\(f1(u)) s).",
                    [("median_gap_s", g), ("usual_gap_s", sig3(u)), ("change_pct", sig3(d))])
            }
        }
        // rallies
        if let s = r.medianShots {
            if s < shortRallyShots {
                add("rallies", 55, "short_rallies", .tip, "Short rallies", "Rallies were short (median \(f0(s)) shots). Try longer cooperative rallies next time to build consistency.",
                    [("median_shots", s), ("rallies", Double(r.rallies))])
            } else if s >= longRallyShots {
                add("rallies", 58, "long_rallies", .praise, "Long rallies", "Median \(f0(s)) shots per rally, longest \(r.longestShots). That's sustained play.",
                    [("median_shots", s), ("longest_shots", Double(r.longestShots))])
            }
        }
        // consistency of the power swings (as in the style profile)
        if let cv = r.powerCV {
            if cv < steadyCV {
                add("consistency", 52, "consistency", .praise, "Steady power", "Your harder swings were very even (variation \(f0(cv * 100)) %).", [("power_cv", sig3(cv))])
            } else if cv > wildCV {
                add("consistency", 45, "consistency", .tip, "Power varies a lot",
                    "Your harder swings varied by \(f0(cv * 100)) %. Repeating one stroke at a steady effort is a simple drill for consistency.", [("power_cv", sig3(cv))])
            }
        }
        // the sensor's limit
        if r.clippedFrac > clippedFrac {
            add("clipping", 65, "clipping", .info, "Faster than the sensor",
                "\(f0(r.clippedFrac * 100)) % of swings exceeded the sensor's range, so real speeds are higher than shown.", [("clipped_frac", r.clippedFrac)])
        }
        // heart rate, when there is one (no zones or medical claims: the numbers only)
        if let a = r.avgBpm, let mx = r.maxBpm {
            add("hr", 40, "hr_summary", .info, "Heart rate", "Your heart rate averaged \(f0(a)) bpm and peaked at \(f0(mx)) bpm.", [("avg_bpm", a), ("max_bpm", mx)])
        }
        // the weekly goal
        if let g = ctx.weeklyGoal, g.target > 0 {
            if g.value >= g.target {
                add("volume", 62, "volume_trend", .praise, "Weekly goal reached", "This session takes you to \(f0(g.value)) of \(f0(g.target)) \(g.unit) this week.",
                    [("week_value", g.value), ("week_target", g.target)])
            } else {
                add("volume", 35, "volume_trend", .info, "Toward your weekly goal", "\(f0(g.target - g.value)) \(g.unit) to go this week (\(f0(g.value)) of \(f0(g.target))).",
                    [("week_value", g.value), ("week_target", g.target)])
            }
        }
        // strokes: only when a model ran (experimental)
        if let st = r.strokes {
            let classified = ["power", "mid", "touch"].reduce(0) { $0 + (st.groups[$1] ?? 0) }
            if classified >= minSwings {
                let touch = Double(st.groups["touch"] ?? 0) / Double(classified)
                if touch < lowTouchShare {
                    add("strokes", 48, "stroke_variety", .tip, "Mostly full swings",
                        "Only \(f0(touch * 100)) % of your strokes were touch shots (net, block, short serve; experimental). Mixing in soft shots makes play harder to read.",
                        [("touch_share", sig3(touch)), ("classified", Double(classified))])
                }
            }
        }
        // the top few, one per family
        var seen = Set<String>(), out: [I] = []
        for x in c.sorted(by: { $0.priority > $1.priority }) where !seen.contains(x.family) {
            seen.insert(x.family)
            out.append(x.insight)
            if out.count == maxInsights { break }
        }
        return out
    }
}
