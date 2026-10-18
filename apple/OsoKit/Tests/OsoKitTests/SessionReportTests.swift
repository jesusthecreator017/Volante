import Foundation
import Testing
@testable import OsoKit

private func repo(_ rel: String) throws -> Data {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("../../../..").standardized
    return try Data(contentsOf: root.appendingPathComponent(rel))
}

private func vectorReport() throws -> SessionReport {
    let cap = try ImuFile.parse([UInt8](try repo("arena/kit/tests/data/twins_vector.imu.bin")))
    return SessionReporter.build(parts: [cap.samples], startedAt: "2026-10-17T18:00:00Z", mount: .identity)
}

@Test func reportMatchesThePythonReferenceByteForByte() throws {
    let want = String(decoding: try repo("tools/strokes/data/report_vector.json"), as: UTF8.self)
    #expect(try vectorReport().json() == want)
}

/// A synthetic session: `n` swings, `gap` s apart, peak dps from `dps(i)`, as racquet-frame half-sines (identity mount).
private func session(_ n: Int, gap: Double = 1.8, dps: (Int) -> Double) -> SessionReport {
    let rate = 500.0, k = Double.pi / 180
    let total = Double(n) * gap + 2
    var peaks: [(Double, Double)] = []
    for i in 0..<n { peaks.append((1 + Double(i) * gap, dps(i))) }
    var samples: [Sample] = []
    var j = 0
    for s in 0..<Int(total * rate) {
        let t = Double(s) / rate
        while j < peaks.count - 1, t > peaks[j].0 + 0.2 { j += 1 }
        let (t0, p) = peaks[j]
        let w = (t >= t0 && t < t0 + 0.1) ? p * sin((t - t0) / 0.1 * .pi) : 0
        samples.append(Sample(t: t, acc: Vec3(0, 0, 9.81), gyro: Vec3(Double(Float(w * k)), 0, 0)))
    }
    return SessionReporter.build(parts: [samples], startedAt: "2026-10-17T18:00:00Z", mount: .identity)
}

@Test func fewerThan30SwingsSaysPlayLonger() {
    let r = session(12) { _ in 900 }
    #expect(r.swings == 12)
    #expect(InsightRules.insights(for: r).map(\.id) == ["play_longer"])
}

@Test func speedFadeAndShortRalliesFire() {
    // 45 swings: the last third 30 % softer; 3 swings per rally (a 7 s pause every 3rd swing breaks the rally)
    let r = session(45, gap: 1.8) { i in i >= 30 ? 700 : 1000 }
    #expect(r.swings == 45)
    let ids = InsightRules.insights(for: r).map(\.id)
    #expect(ids.contains("speed_fade"))
    let fade = InsightRules.insights(for: r).first { $0.id == "speed_fade" }!
    #expect(fade.evidence.map(\.0) == ["first_third_p90_kmh", "last_third_p90_kmh", "drop_pct"])
}

@Test func thresholdsAndHistory() {
    let r = session(40) { _ in 1000 }  // even power: steady
    var ids = InsightRules.insights(for: r).map(\.id)
    #expect(ids.contains("consistency") && !ids.contains("speed_fade") && !ids.contains("new_best"))
    // a faster previous best -> nothing about bests; a slower one -> new_best
    var slower = r; slower.maxKmh = (r.maxKmh ?? 0) * 0.8
    ids = InsightRules.insights(for: r, .init(history: [slower])).map(\.id)
    #expect(ids.contains("new_best"))
    var close = r; close.maxKmh = (r.maxKmh ?? 0) * 1.02
    #expect(InsightRules.insights(for: r, .init(history: [close])).map(\.id).contains("near_best"))
    // tempo: 3 earlier sessions 30 % slower -> quicker than usual
    var slow = r; slow.medianGapS = (r.medianGapS ?? 1) * 1.3
    let t = InsightRules.insights(for: r, .init(history: [slow, slow, slow])).first { $0.id == "tempo_vs_usual" }
    #expect(t?.title == "Quicker than usual")
}

@Test func rankingCapsAtFourOneFamilyEach() {
    var r = session(40) { _ in 1000 }
    r.clippedFrac = 0.3; r.avgBpm = 140; r.maxBpm = 170; r.medianShots = 2
    var slower = r; slower.maxKmh = 1
    let out = InsightRules.insights(for: r, .init(history: [slower], weeklyGoal: (3, 3, "sessions")))
    #expect(out.count == 4)
    #expect(Set(out.map(\.id)).count == 4)
    #expect(!out.contains { $0.id == "new_best" })  // clipped sessions never claim a best
}

@Test func strokeRulesOnlyWithAModel() {
    var r = session(40) { _ in 1000 }
    #expect(!InsightRules.insights(for: r).contains { $0.id == "stroke_variety" })
    r.strokes = .init(groups: ["power": 30, "mid": 20, "touch": 2, "unclassified": 3])
    #expect(InsightRules.insights(for: r).contains { $0.id == "stroke_variety" })
    #expect(r.json().contains("\"experimental\": true"))
}

@Test func noWeeklyGoalNoGoalInsight() {  // the web upload passes no goal, so its reports never carry one
    let r = session(40) { _ in 1000 }
    #expect(!InsightRules.insights(for: r).contains { $0.id == "volume_trend" })
    #expect(InsightRules.insights(for: r, .init(weeklyGoal: (1, 3, "sessions"))).contains { $0.id == "volume_trend" })
}
