// Volante Twins style profile `volante-style/1` (arena/contract/STYLE_PROFILE.md, docs/twins-app.md): the phone's port of
// `oso-rec profile` (crates/oso-replay/src/imu/profile.rs). The constants are the contract; equal input gives the same bytes as
// the Rust reference (test vector arena/kit/tests/data/twins_vector.*). Raw sessions never leave the phone: only this file is shared.

import Foundation

public enum StyleProfileError: Error, Equatable, CustomStringConvertible {
    case noSwings
    case noTempo
    case invalid(String)

    public var description: String {
        switch self {
        case .noSwings: "No swings found yet. Record a session with the pod first."
        case .noTempo: "Not enough play to measure your tempo: you need at least two swings within 6 seconds of each other."
        case .invalid(let why): "The profile did not validate: \(why)"
        }
    }
}

/// Sensor -> racquet frame for the profile: rows are the racquet X, Y (shaft), Z (face normal) axes in the sensor frame.
public struct StyleMount: Equatable, Sendable {
    public var rows: (Vec3, Vec3, Vec3)
    public init(rows: (Vec3, Vec3, Vec3)) { self.rows = rows }

    /// The synthetic data and the test vector: sensor axes = racquet axes.
    public static let identity = StyleMount(rows: (Vec3(1, 0, 0), Vec3(0, 1, 0), Vec3(0, 0, 1)))
    /// The pod as built (the default for real sessions): racquet X = sensor Y, shaft = sensor Z, face normal = sensor X.
    public static let flat = StyleMount(rows: (Vec3(0, 1, 0), Vec3(0, 0, 1), Vec3(1, 0, 0)))

    var isIdentity: Bool { self == .identity }

    public static func == (a: StyleMount, b: StyleMount) -> Bool { a.rows.0 == b.rows.0 && a.rows.1 == b.rows.1 && a.rows.2 == b.rows.2 }

    /// Rotates like `Mount::sample` in Rust: f64 math on the f32 values, rounded back to f32.
    func apply(_ v: Vec3) -> Vec3 {
        let f = { (x: Double) in Double(Float(x)) }
        return Vec3(f(dot(rows.0, v)), f(dot(rows.1, v)), f(dot(rows.2, v)))
    }
}

public struct StyleProfile: Equatable, Sendable, Codable {
    public struct Source: Equatable, Sendable, Codable {
        public var sessions: Int, swings: Int, minutes: Double
        public var podFw: String, tool: String
        enum CodingKeys: String, CodingKey { case sessions, swings, minutes, podFw = "pod_fw", tool }
    }
    public struct Speed: Equatable, Sendable, Codable {
        public var p50Kmh: Double, p90Kmh: Double, cv: Double, clippedFrac: Double
        enum CodingKeys: String, CodingKey { case p50Kmh = "p50_kmh", p90Kmh = "p90_kmh", cv, clippedFrac = "clipped_frac" }
    }
    public struct Tempo: Equatable, Sendable, Codable {
        public var medianGapS: Double, p25GapS: Double, p75GapS: Double
        enum CodingKeys: String, CodingKey { case medianGapS = "median_gap_s", p25GapS = "p25_gap_s", p75GapS = "p75_gap_s" }
    }
    public var schema = StyleProfile.schemaName
    public var createdAt: String
    public var source: Source
    public var speed: Speed
    public var tempo: Tempo
    enum CodingKeys: String, CodingKey { case schema, createdAt = "created_at", source, speed, tempo }

    public static let schemaName = "volante-style/1"
    public static let thresholdDps = 300.0
    /// Window after a swing's onset in which its peak speed is taken (= the swing counter's refractory time)
    public static let windowS = 0.8
    /// A longer gap between swings ends the rally
    public static let rallySplitS = 6.0
    public static let headCenterM = 0.53
    public static let maxBytes = 8192
    public static let notes = "speeds are estimates (gyro x 0.53 m) and lower bounds when clipped"

    /// The file, byte for byte like `oso-rec profile` (serde_json pretty print, 2 spaces, a trailing newline).
    public func json() -> String {
        func obj(_ pairs: [(String, String)], _ indent: String) -> String {
            "{\n" + pairs.map { "\(indent)  \"\($0.0)\": \($0.1)" }.joined(separator: ",\n") + "\n\(indent)}"
        }
        let str = { (s: String) in "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\"" }
        let top: [(String, String)] = [
            ("schema", str(schema)),
            ("created_at", str(createdAt)),
            ("source", obj([("sessions", "\(source.sessions)"), ("swings", "\(source.swings)"), ("minutes", jsonNumber(source.minutes)),
                            ("pod_fw", str(source.podFw)), ("tool", str(source.tool))], "  ")),
            ("speed", obj([("p50_kmh", jsonNumber(speed.p50Kmh)), ("p90_kmh", jsonNumber(speed.p90Kmh)), ("cv", jsonNumber(speed.cv)),
                           ("clipped_frac", jsonNumber(speed.clippedFrac))], "  ")),
            ("tempo", obj([("median_gap_s", jsonNumber(tempo.medianGapS)), ("p25_gap_s", jsonNumber(tempo.p25GapS)),
                           ("p75_gap_s", jsonNumber(tempo.p75GapS))], "  ")),
            ("stroke_mix", "null"),
            ("side_mix", "null"),
            ("notes", str(Self.notes)),
        ]
        return obj(top, "") + "\n"
    }
}

/// serde_json's float formatting (shortest round-trip, always with a fractional part or exponent): 2.0, 0.342, 33.3.
func jsonNumber(_ x: Double) -> String {
    var s = "\(x)"
    if s.contains("e") {  // Swift "1e-05" -> serde "1e-5"
        let parts = s.split(separator: "e")
        let exp = Int(parts[1]) ?? 0
        s = "\(parts[0])e\(exp)"
    }
    return s
}

/// 3 significant digits, half away from zero (Rust `sig3`).
public func sig3(_ x: Double) -> Double {
    if x == 0 || !x.isFinite { return x }
    let p = 2 - Int(log10(abs(x)).rounded(.down))
    let k = pow(10.0, Double(abs(p)))
    return p >= 0 ? (x * k).rounded() / k : (x / k).rounded() * k
}

/// Linear-interpolation percentile (numpy default) of a sorted, non-empty array.
func percentile(_ sorted: [Double], _ q: Double) -> Double {
    let pos = q * Double(sorted.count - 1)
    let lo = Int(pos.rounded(.down)), hi = Int(pos.rounded(.up))
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - Double(lo))
}

public struct ProfileSwing: Equatable, Sendable {
    public var t: Double, kmh: Double, clipped: Bool
}

/// The guided-collection swing counter (Rust swingdet), at the profile's 300 dps: 20 ms smoothing, re-arm after 0.8 s below 150 dps.
struct ProfileSwingCounter {
    private var win: [(t: Double, dps: Double)] = []
    private var head = 0
    private var sum = 0.0
    private var armed = true
    private var last: Double?
    let threshold: Double

    init(threshold: Double) { self.threshold = threshold }

    mutating func push(t: Double, gyro: Vec3) -> Bool {
        let dps = (gyro.x * gyro.x + gyro.y * gyro.y + gyro.z * gyro.z).squareRoot() * (180.0 / Double.pi)
        win.append((t, dps))
        sum += dps
        while head < win.count, t - win[head].t >= 0.020 {
            sum -= win[head].dps
            head += 1
        }
        if head > 4096 { win.removeFirst(head); head = 0 }
        let v = sum / Double(win.count - head)
        if !armed {
            armed = v < 150 && (last.map { t - $0 >= 0.8 } ?? true)
        } else if v >= threshold {
            armed = false
            last = t
            return true
        }
        return false
    }
}

public enum StyleProfiler {
    /// Swings of one recording part (Rust `profile::swings`), samples rotated by `mount`.
    public static func swings(_ samples: [Sample], mount: StyleMount = .flat) -> [ProfileSwing] {
        let gyro = mount.isIdentity ? samples.map(\.gyro) : samples.map { mount.apply($0.gyro) }
        var counter = ProfileSwingCounter(threshold: StyleProfile.thresholdDps)
        var out: [ProfileSwing] = []
        for i in samples.indices where counter.push(t: samples[i].t, gyro: gyro[i]) {
            let t = samples[i].t
            var peak = 0.0, clipped = false
            var j = i
            while j < samples.count, samples[j].t < t + StyleProfile.windowS {
                peak = max(peak, hypot(gyro[j].x, gyro[j].z))
                clipped = clipped || samples[j].saturated
                j += 1
            }
            out.append(ProfileSwing(t: t, kmh: peak * StyleProfile.headCenterM * 3.6, clipped: clipped))
        }
        return out
    }

    /// One session = its recording parts in order (each on its own device clock); `podFw` when known.
    public struct Input: Sendable {
        public var parts: [[Sample]]
        public var podFw: String?
        public init(parts: [[Sample]], podFw: String? = nil) { self.parts = parts; self.podFw = podFw }
    }

    /// The profile of these sessions. `createdAt` like 2026-10-17T18:00:00Z, `tool` like "Volante iOS 0.1".
    public static func compute(_ inputs: [Input], createdAt: String, tool: String, mount: StyleMount = .flat) throws -> StyleProfile {
        var sw: [ProfileSwing] = [], gaps: [Double] = [], minutes = 0.0
        for part in inputs.flatMap(\.parts) {
            if let a = part.first, let b = part.last { minutes += (b.t - a.t) / 60 }
            let s = swings(part, mount: mount)
            if s.count > 1 {
                for k in 1..<s.count where s[k].t - s[k - 1].t <= StyleProfile.rallySplitS { gaps.append(s[k].t - s[k - 1].t) }
            }
            sw += s
        }
        guard !sw.isEmpty else { throw StyleProfileError.noSwings }
        guard !gaps.isEmpty else { throw StyleProfileError.noTempo }
        let speeds = sw.map(\.kmh).sorted()
        let p50 = percentile(speeds, 0.5)
        let power = speeds.drop { $0 < p50 }
        let pn = Double(power.count)
        let mean = power.reduce(0, +) / pn
        let std = (power.map { ($0 - mean) * ($0 - mean) }.reduce(0, +) / pn).squareRoot()
        let g = gaps.sorted()
        var fws = Set<String>()
        for fw in inputs.compactMap(\.podFw) {
            let clean = String(fw.prefix(32).map { $0.isASCII && ($0.isLetter || $0.isNumber || ".-_".contains($0)) ? $0 : "_" })
            if !clean.isEmpty { fws.insert(clean) }
        }
        let p = StyleProfile(
            createdAt: createdAt,
            source: .init(sessions: inputs.count, swings: sw.count, minutes: sig3(minutes),
                          podFw: fws.isEmpty ? "unknown" : fws.sorted().joined(separator: ","), tool: tool),
            speed: .init(p50Kmh: sig3(p50), p90Kmh: sig3(percentile(speeds, 0.9)), cv: sig3(mean > 0 ? std / mean : 0),
                         clippedFrac: sig3(Double(sw.filter(\.clipped).count) / Double(speeds.count))),
            tempo: .init(medianGapS: sig3(percentile(g, 0.5)), p25GapS: sig3(percentile(g, 0.25)), p75GapS: sig3(percentile(g, 0.75))))
        if let why = validate(p) { throw StyleProfileError.invalid(why) }
        return p
    }

    /// The section 1 rules the computed numbers must meet (nil = valid).
    public static func validate(_ p: StyleProfile) -> String? {
        let inRange = { (x: Double, lo: Double, hi: Double) in x.isFinite && x >= lo && x <= hi }
        let ident = { (s: String) in (1...64).contains(s.count) && s.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || " ._-".contains($0)) } }
        if !(1...10_000_000).contains(p.source.sessions) || !(1...10_000_000).contains(p.source.swings) { return "counts out of range" }
        if !(p.source.minutes > 0 && p.source.minutes <= 10_000_000) { return "minutes out of range" }
        if !ident(p.source.podFw) || !ident(p.source.tool) { return "pod_fw / tool must be 1-64 of A-Z a-z 0-9 . _ - space" }
        if !inRange(p.speed.p50Kmh, 0, 400) || !inRange(p.speed.p90Kmh, 0, 400) || p.speed.p50Kmh > p.speed.p90Kmh { return "speeds out of range" }
        if !inRange(p.speed.cv, 0, 2) || !inRange(p.speed.clippedFrac, 0, 1) { return "cv / clipped_frac out of range" }
        let t = p.tempo
        if !inRange(t.medianGapS, 0.2, 10) || !inRange(t.p25GapS, 0.2, 10) || !inRange(t.p75GapS, 0.2, 10)
            || !(t.p25GapS <= t.medianGapS && t.medianGapS <= t.p75GapS) { return "tempo out of range" }
        if p.json().utf8.count > StyleProfile.maxBytes { return "over 8192 bytes" }
        return nil
    }

    // MARK: build mapping (STYLE_PROFILE.md section 3, oso_arena.style.build_from_profile)

    public struct ArenaBuild: Equatable, Sendable { public var power, speed, reach, accuracy: Int }

    static func lin(_ x: Double, _ x1: Double, _ v1: Double, _ x2: Double, _ v2: Double) -> Double { v1 + (x - x1) * (v2 - v1) / (x2 - x1) }

    /// The build the Arena forces on a twin of this profile (four stats 1-10, sum 24).
    public static func build(_ p: StyleProfile) -> ArenaBuild {
        let raw: [String: Double] = [
            "power": lin(p.speed.p90Kmh, 40, 1, 120, 10),
            "accuracy": lin(p.speed.cv, 0.30, 1, 0.05, 10),
            "speed": lin(p.tempo.medianGapS, 3.0, 1, 1.0, 10),
        ]
        let order = ["power", "speed", "accuracy"]
        var b = [String: Int]()
        for k in order { b[k] = min(max(Int((raw[k]! + 0.5).rounded(.down)), 1), 10) }
        b["reach"] = min(max(24 - order.reduce(0) { $0 + b[$1]! }, 1), 10)
        while true {
            let diff = 24 - b.values.reduce(0, +)
            if diff == 0 { break }
            if diff < 0 {  // too many points: take from the largest that can drop (ties: larger raw, then order)
                let k = order.filter { b[$0]! > 1 }.max { (b[$0]!, raw[$0]!, -order.firstIndex(of: $0)!) < (b[$1]!, raw[$1]!, -order.firstIndex(of: $1)!) }!
                b[k]! -= 1
            } else {  // too few: give to the smallest that can rise (ties: smaller raw, then order)
                let k = order.filter { b[$0]! < 10 }.min { (b[$0]!, raw[$0]!, order.firstIndex(of: $0)!) < (b[$1]!, raw[$1]!, order.firstIndex(of: $1)!) }!
                b[k]! += 1
            }
        }
        return ArenaBuild(power: b["power"]!, speed: b["speed"]!, reach: b["reach"]!, accuracy: b["accuracy"]!)
    }
}
