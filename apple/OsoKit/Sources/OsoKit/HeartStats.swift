// Heart-rate statistics for a session, the way fitness apps show them: average / max, time in 5 zones of max heart rate,
// an effort score (Edwards' TRIMP: minutes in each zone x 1..5) and 1-minute heart-rate recovery after play.
// Pure math on wall-clock samples (HeartRateSample.t is epoch seconds), so the watch, Fitbit, Apple Health and imported
// files all go through the same code. Descriptive only: no medical interpretation.

import Foundation

public struct HeartStats: Equatable, Sendable {
    /// Zone lower bounds as a share of max heart rate (zone 1 = 50-60 %, ..., zone 5 = 90 %+). Below 50 % counts as no zone.
    public static let zoneFloors = [0.5, 0.6, 0.7, 0.8, 0.9]
    public static let zoneNames = ["Warm-up", "Easy", "Aerobic", "Threshold", "Maximum"]
    /// A sample stands for at most this long (gaps in the data are not counted as time in a zone).
    public static let maxSampleSpan = 30.0

    public var avg: Double
    public var max: Double
    public var min: Double
    /// seconds in zones 1-5
    public var zoneSeconds: [Double]
    /// Edwards TRIMP: sum over zones of minutes x (zone number)
    public var effort: Double
    /// bpm drop from the end of play to 60 s later (nil without data after the session)
    public var recovery: Double?
    public var samples: Int
    public var maxHR: Double

    public init(avg: Double, max: Double, min: Double, zoneSeconds: [Double], effort: Double, recovery: Double?, samples: Int, maxHR: Double) {
        self.avg = avg; self.max = max; self.min = min; self.zoneSeconds = zoneSeconds; self.effort = effort
        self.recovery = recovery; self.samples = samples; self.maxHR = maxHR
    }

    public var zoneTotal: Double { zoneSeconds.reduce(0, +) }

    /// Light < 50, moderate < 100, hard < 200, very hard above (Edwards points for a session).
    public var effortLabel: String {
        effort < 50 ? "Light" : effort < 100 ? "Moderate" : effort < 200 ? "Hard" : "Very hard"
    }

    /// `samples`: heart rate around the session, sorted or not. `start`/`end`: the session's wall-clock span; samples outside it
    /// only feed the recovery. `maxHR`: the player's maximum heart rate (estimate when unknown).
    public static func compute(_ samples: [HeartRateSample], start: Double, end: Double, maxHR: Double) -> HeartStats? {
        let all = samples.sorted { $0.t < $1.t }
        let inside = all.filter { $0.t >= start && $0.t < end }  // [start, end): a reading at the end belongs to the recovery
        guard !inside.isEmpty, maxHR > 0 else { return nil }
        var zones = [Double](repeating: 0, count: 5)
        for (i, s) in inside.enumerated() {
            let next = i + 1 < inside.count ? inside[i + 1].t : Swift.min(end, s.t + maxSampleSpan)
            let dt = Swift.max(0, Swift.min(next - s.t, maxSampleSpan))
            if let z = zone(s.bpm, maxHR: maxHR) { zones[z] += dt }
        }
        let effort = zip(zones, 1...5).reduce(0.0) { $0 + $1.0 / 60 * Double($1.1) }
        // recovery: the mean of the last 15 s of play minus the mean around end + 60 s (+-10 s)
        let atEnd = inside.filter { $0.t >= end - 15 }.map(\.bpm)
        let later = all.filter { $0.t >= end + 50 && $0.t <= end + 70 }.map(\.bpm)
        let rec: Double? = atEnd.isEmpty || later.isEmpty ? nil : mean(atEnd) - mean(later)
        let bpm = inside.map(\.bpm)
        return HeartStats(avg: mean(bpm), max: bpm.max()!, min: bpm.min()!, zoneSeconds: zones, effort: effort,
                          recovery: rec, samples: inside.count, maxHR: maxHR)
    }

    /// 0...4 for zones 1...5, nil below 50 % of max.
    public static func zone(_ bpm: Double, maxHR: Double) -> Int? {
        let f = bpm / maxHR
        return zoneFloors.lastIndex { f >= $0 }
    }

    /// The max heart rate to use: the player's setting, else 220 - age, else the highest seen (at least 185).
    public static func maxHR(setting: Double?, age: Int?, observedPeak: Double?) -> Double {
        if let s = setting, s >= 120, s <= 230 { return s }
        if let a = age, a >= 8, a <= 100 { return Double(220 - a) }
        return Swift.max(185, observedPeak ?? 0)
    }

    static func mean(_ v: [Double]) -> Double { v.reduce(0, +) / Double(v.count) }
}

// MARK: importing heart rate from a file

public enum HeartRateFile {
    public enum Failure: Error, Equatable, CustomStringConvertible {
        case empty, unreadable(String)
        public var description: String {
            switch self {
            case .empty: "No heart-rate rows found."
            case .unreadable(let why): "Couldn't read the file: \(why)"
            }
        }
    }

    /// CSV (`time,bpm` with a header or not; time as ISO 8601 or epoch seconds/milliseconds) or JSON (`[{"t"|"time"|"timestamp",
    /// "bpm"|"value"|"heart_rate"}]`, or Fitbit's `{"activities-heart-intraday": {"dataset": [{"time": "HH:MM:SS", "value"}]}}` with
    /// `day` giving the date). Rows outside 25-250 bpm are dropped.
    public static func parse(_ data: Data, day: Date? = nil) throws -> [HeartRateSample] {
        let out: [HeartRateSample]
        if let j = try? JSONSerialization.jsonObject(with: data) {
            out = try json(j, day: day)
        } else if let text = String(data: data, encoding: .utf8) {
            out = csv(text)
        } else {
            throw Failure.unreadable("not text")
        }
        let ok = out.filter { $0.bpm >= 25 && $0.bpm <= 250 && $0.t > 0 }.sorted { $0.t < $1.t }
        guard !ok.isEmpty else { throw Failure.empty }
        return ok
    }

    static func time(_ v: Any?, day: Date?) -> Double? {
        if let n = v as? Double { return n > 1e12 ? n / 1000 : n }
        if let n = v as? Int { return Double(n) > 1e12 ? Double(n) / 1000 : Double(n) }
        guard let s = (v as? String)?.trimmingCharacters(in: .whitespaces), !s.isEmpty else { return nil }
        if let n = Double(s) { return n > 1e12 ? n / 1000 : n }
        let iso = ISO8601DateFormatter()
        for opts: ISO8601DateFormatter.Options in [[.withInternetDateTime], [.withInternetDateTime, .withFractionalSeconds],
                                                   [.withFullDate, .withTime, .withColonSeparatorInTime, .withDashSeparatorInDate]] {
            iso.formatOptions = opts
            if let d = iso.date(from: s) { return d.timeIntervalSince1970 }
        }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        for p in ["yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd'T'HH:mm:ss", "MM/dd/yyyy HH:mm:ss", "MM/dd/yy HH:mm:ss"] {
            f.dateFormat = p
            if let d = f.date(from: s) { return d.timeIntervalSince1970 }
        }
        if let day, s.count <= 8 {  // "HH:MM:SS" on the given day (Fitbit intraday)
            f.dateFormat = "HH:mm:ss"
            if let t = f.date(from: s) {
                let c = Calendar.current.dateComponents([.hour, .minute, .second], from: t)
                return Calendar.current.date(bySettingHour: c.hour ?? 0, minute: c.minute ?? 0, second: c.second ?? 0, of: day)?.timeIntervalSince1970
            }
        }
        return nil
    }

    static func bpm(_ v: Any?) -> Double? {
        if let n = v as? Double { return n }
        if let n = v as? Int { return Double(n) }
        if let s = v as? String { return Double(s.trimmingCharacters(in: .whitespaces)) }
        if let d = v as? [String: Any] { return bpm(d["bpm"] ?? d["value"]) }
        return nil
    }

    static func json(_ j: Any, day: Date?) throws -> [HeartRateSample] {
        var rows: [Any] = []
        if let a = j as? [Any] { rows = a }
        else if let d = j as? [String: Any] {
            if let intra = d["activities-heart-intraday"] as? [String: Any], let ds = intra["dataset"] as? [Any] { rows = ds }
            else if let a = (d["samples"] ?? d["items"] ?? d["data"] ?? d["heartRate"]) as? [Any] { rows = a }
        }
        guard !rows.isEmpty else { throw Failure.unreadable("no list of samples in the JSON") }
        return rows.compactMap { r -> HeartRateSample? in
            guard let o = r as? [String: Any],
                  let t = time(o["t"] ?? o["time"] ?? o["timestamp"] ?? o["dateTime"] ?? o["date"], day: day),
                  let b = bpm(o["bpm"] ?? o["value"] ?? o["heart_rate"] ?? o["heartRate"] ?? o["hr"]) else { return nil }
            return HeartRateSample(t: t, bpm: b)
        }
    }

    static func csv(_ text: String) -> [HeartRateSample] {
        var out: [HeartRateSample] = []
        var tCol = 0, bCol = 1
        for (i, line) in text.split(whereSeparator: \.isNewline).enumerated() {
            let cells = line.split(separator: line.contains(";") ? ";" : ",", omittingEmptySubsequences: false)
                .map { $0.trimmingCharacters(in: CharacterSet(charactersIn: " \"")) }
            if i == 0, cells.contains(where: { Double($0) == nil && time($0, day: nil) == nil }) {  // a header row: find the columns
                let low = cells.map { $0.lowercased() }
                tCol = low.firstIndex { $0.contains("time") || $0.contains("date") || $0 == "t" } ?? 0
                bCol = low.firstIndex { $0.contains("bpm") || $0.contains("heart") || $0.contains("value") || $0 == "hr" } ?? 1
                continue
            }
            guard cells.count > max(tCol, bCol), let t = time(cells[tCol], day: nil), let b = Double(cells[bCol]) else { continue }
            out.append(HeartRateSample(t: t, bpm: b))
        }
        return out
    }
}
