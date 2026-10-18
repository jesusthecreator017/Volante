import Foundation
import Testing
@testable import OsoKit

/// The repo's shared fixtures (the Rust reference and the kit use the same files).
private func repoFile(_ rel: String) throws -> Data {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("../../../..").standardized
    return try Data(contentsOf: root.appendingPathComponent(rel))
}

@Test func styleProfileMatchesTheRustReferenceByteForByte() throws {
    let imu = try ImuFile.parse([UInt8](try repoFile("arena/kit/tests/data/twins_vector.imu.bin")))
    let want = String(decoding: try repoFile("arena/kit/tests/data/twins_vector.profile.json"), as: UTF8.self)
    let p = try StyleProfiler.compute([.init(parts: [imu.samples])], createdAt: "2026-10-17T18:00:00Z", tool: "oso-rec 0.1.0", mount: .identity)
    #expect(p.json() == want)
    #expect(p.source.swings == 8 && p.speed.p50Kmh == 33.3 && p.speed.p90Kmh == 60.3 && p.speed.cv == 0.409)
    #expect(p.speed.clippedFrac == 0.125 && p.tempo.medianGapS == 2.0 && p.tempo.p25GapS == 1.81 && p.tempo.p75GapS == 2.34)
    #expect(p.source.minutes == 0.342)
}

@Test func flatMountHeadSpeed() {
    // docs/twins-app.md: 1000 dps about the sensor X axis for a 100 ms half-sine -> perp peak 1000 dps = 33.3 km/h
    let rate = 500.0, k = Double.pi / 180
    var samples: [Sample] = []
    for i in 0..<1500 {
        let t = Double(i) / rate
        let w = (t >= 1 && t < 1.1) ? 1000 * sin((t - 1) / 0.1 * .pi) : 0
        samples.append(Sample(t: t, acc: Vec3(0, 0, 9.81), gyro: Vec3(Double(Float(w * k)), 0, 0)))
    }
    let s = StyleProfiler.swings(samples, mount: .flat)
    #expect(s.count == 1)
    #expect(sig3(s[0].kmh) == 33.3)
}

@Test func buildMappingTable() {
    // STYLE_PROFILE.md section 3: (p90, cv, median gap) -> power, speed, reach, accuracy
    let cases: [(Double, Double, Double, [Int])] = [
        (84, 0.12, 1.9, [6, 6, 5, 7]), (80, 0.15, 2.0, [6, 6, 6, 6]), (40, 0.30, 3.0, [5, 5, 10, 4]),
        (120, 0.05, 1.0, [7, 8, 1, 8]), (60, 0.20, 1.5, [3, 8, 8, 5]), (110, 0.22, 2.8, [9, 2, 9, 4]),
    ]
    for (p90, cv, gap, want) in cases {
        let p = StyleProfile(createdAt: "2026-10-17T18:00:00Z",
                             source: .init(sessions: 1, swings: 100, minutes: 10, podFw: "unknown", tool: "t"),
                             speed: .init(p50Kmh: min(p90, 30), p90Kmh: p90, cv: cv, clippedFrac: 0),
                             tempo: .init(medianGapS: gap, p25GapS: gap * 0.8, p75GapS: gap * 1.2))
        let b = StyleProfiler.build(p)
        #expect([b.power, b.speed, b.reach, b.accuracy] == want, "p90 \(p90) cv \(cv) gap \(gap)")
    }
}

@Test func refusesTooLittlePlay() {
    let still = (0..<1000).map { Sample(t: Double($0) / 500, acc: Vec3(0, 0, 9.81), gyro: Vec3(0, 0, 0)) }
    #expect(throws: StyleProfileError.noSwings) { try StyleProfiler.compute([.init(parts: [still])], createdAt: "2026-10-17T18:00:00Z", tool: "t") }
}

@Test func sig3Rounding() {
    #expect(sig3(33.349) == 33.3 && sig3(0.34249) == 0.342 && sig3(1234.5) == 1230 && sig3(-0.12345) == -0.123 && sig3(0) == 0)
    #expect(jsonNumber(2.0) == "2.0" && jsonNumber(0.342) == "0.342")
}
