import Foundation
import Testing
@testable import OsoKit

@Test func zonesEffortAndRecovery() {
    // 10 min at 150 bpm with max 190 (79 % -> zone 3), then 2 min at 175 (92 % -> zone 5), then recovery to 140 a minute later
    var s: [HeartRateSample] = []
    for i in 0..<600 where i % 5 == 0 { s.append(.init(t: 1000 + Double(i), bpm: 150)) }
    for i in 600..<720 where i % 5 == 0 { s.append(.init(t: 1000 + Double(i), bpm: 175)) }
    for i in 720..<800 where i % 5 == 0 { s.append(.init(t: 1000 + Double(i), bpm: 140)) }
    let h = HeartStats.compute(s, start: 1000, end: 1720, maxHR: 190)!
    #expect(abs(h.zoneSeconds[2] - 600) < 6 && abs(h.zoneSeconds[4] - 120) < 6)
    #expect(abs(h.effort - (10 * 3 + 2 * 5)) < 0.6)
    #expect(h.max == 175 && h.recovery == 35)
    #expect(h.effortLabel == "Light")
}

@Test func gapsDoNotCountAsZoneTime() {
    let s = [HeartRateSample(t: 0, bpm: 150), HeartRateSample(t: 600, bpm: 150)]
    let h = HeartStats.compute(s, start: 0, end: 600, maxHR: 190)!
    #expect(h.zoneTotal <= 60)  // two samples, each standing for at most 30 s
    #expect(h.recovery == nil)
}

@Test func maxHRChoice() {
    #expect(HeartStats.maxHR(setting: 195, age: 30, observedPeak: 170) == 195)
    #expect(HeartStats.maxHR(setting: nil, age: 30, observedPeak: 170) == 190)
    #expect(HeartStats.maxHR(setting: nil, age: nil, observedPeak: 201) == 201)
    #expect(HeartStats.maxHR(setting: nil, age: nil, observedPeak: nil) == 185)
    #expect(HeartStats.zone(80, maxHR: 190) == nil && HeartStats.zone(171, maxHR: 190) == 4)
}

@Test func fileImportFormats() throws {
    let csv = "Time,Heart Rate (bpm)\n2026-10-18T02:30:00Z,120\n2026-10-18T02:30:05Z,124\n2026-10-18T02:30:10Z,999\n"
    let a = try HeartRateFile.parse(Data(csv.utf8))
    #expect(a.count == 2 && a[0].bpm == 120 && a[0].t == 1792290600)
    let plain = "1792290600,118\n1792290605000,119\n"
    #expect(try HeartRateFile.parse(Data(plain.utf8)).map(\.t) == [1792290600, 1792290605])
    let js = #"[{"t": 1792290600, "bpm": 121}, {"timestamp": "2026-10-18T02:30:05Z", "value": 123}]"#
    #expect(try HeartRateFile.parse(Data(js.utf8)).count == 2)
    let fitbit = #"{"activities-heart-intraday": {"dataset": [{"time": "22:30:00", "value": 130}, {"time": "22:30:05", "value": 131}]}}"#
    let day = ISO8601DateFormatter().date(from: "2026-10-17T12:00:00Z")!
    #expect(try HeartRateFile.parse(Data(fitbit.utf8), day: day).count == 2)
    #expect(throws: HeartRateFile.Failure.empty) { try HeartRateFile.parse(Data("time,bpm\n".utf8)) }
}
