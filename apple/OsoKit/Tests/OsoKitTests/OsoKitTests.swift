import Testing
@testable import OsoKit
#if canImport(Darwin)
import Darwin
#endif

/// app/public/sample/imu.bin: the synthetic 10 s session (same acceptance numbers as app/test/session.test.ts).
func sampleBytes() -> [UInt8] {
    let here = "\(#filePath)"
    let root = here.ranges(of: "/apple/OsoKit/").first.map { String(here[..<$0.lowerBound]) } ?? "."
    let path = root + "/app/public/sample/imu.bin"
    guard let f = fopen(path, "rb") else { fatalError("missing \(path)") }
    defer { fclose(f) }
    var out: [UInt8] = []
    var buf = [UInt8](repeating: 0, count: 65536)
    while true {
        let n = fread(&buf, 1, buf.count, f)
        if n <= 0 { break }
        out += buf[0..<n]
    }
    return out
}

@Suite struct PodProtocolTests {
    @Test func decodesTheProtoStatusVector() {
        // proto/src/lib.rs `status_roundtrip`, built from the documented offsets
        var s = PodStatus()
        s.fwMajor = 1; s.fwMinor = 2; s.rateHz = 500; s.batteryRaw = 255
        s.stateBits = PodStatus.Bit.streaming | PodStatus.Bit.imuOk; s.accelG = 16; s.gyroDps = 2000
        s.bufferedPackets = 123_456; s.fifoOverflows = 7; s.eventRaw = PodEvent.uploadDone.rawValue
        s.eventValue = 0xdead_beef; s.stateExt = PodStatus.extSleeping
        let b = s.encoded()
        #expect(b.count == 24)
        #expect(b[20] == 1 && b[21...] == [0, 0, 0])
        #expect(Array(b[9..<13]) == [0x40, 0xe2, 0x01, 0x00])
        let d = PodStatus(b)!
        #expect(d == s)
        #expect(d.streaming && d.imuOk && !d.recording && d.sleeping && d.battery == nil && d.event == .uploadDone)
        #expect(PodStatus(Array(b[0..<23])) == nil)
    }

    @Test func errorsAndNotifications() {
        var s = PodStatus()
        s.eventRaw = PodEvent.error.rawValue; s.eventValue = 3
        #expect(s.error == .needConfirm)
        #expect(packetsPerNotification(mtu: 247) == 13)
        #expect(packetsPerNotification(mtu: 23) == 1)
        #expect(ImuFile.notification([UInt8](repeating: 0, count: 35)) == nil)
        #expect(ImuFile.notification([UInt8](repeating: 0, count: 36))?.count == 2)
    }
}

@Suite struct ImuFileTests {
    @Test func sampleAccountingMatchesTheHandoff() throws {
        let cap = try ImuFile.parse(sampleBytes())
        #expect(cap.header.rateHz == 500)
        #expect(cap.stats == PacketStats(arrivals: 6547, unique: 6534, lost: 66, duplicate: 13, reordered: 99))
        #expect(cap.gaps.count == 44)
    }

    @Test func encodeRoundTripsByteForByte() throws {
        let b = sampleBytes()
        let h = try ImuFile.header(b)
        #expect(ImuFile.encode(rateHz: h.rateHz, packets: try ImuFile.packets(b)) == b)
    }

    @Test func unwrapsSeqAndTimeWraps() {
        let p = { (s: UInt16, t: UInt32) in RawPacket(seq16: s, t32: t, v: [0, 0, 2048, 0, 0, 0]) }
        let cap = ImuFile.assemble(header: ImuHeader(rateHz: 500, mountId: 0), packets: [
            p(65534, 0xffff_f000), p(65535, 0xffff_f7d0), p(1, 0x0000_0770), p(0, 0xffff_ffa0), p(1, 0x0000_0770),
        ])
        #expect(cap.stats == PacketStats(arrivals: 5, unique: 4, lost: 0, duplicate: 1, reordered: 1))
        #expect(cap.samples.map { Int(($0.t - cap.samples[0].t) * 1e6 + 0.5) } == [0, 2000, 4000, 6000])
    }

    @Test func rejectsMalformedFiles() {
        #expect(throws: ImuFileError.badMagic) { try ImuFile.header([UInt8](repeating: 0, count: 16)) }
        var b = ImuFile.encode(rateHz: 500, packets: [])
        b.append(1)
        #expect(throws: ImuFileError.partialPacket(1)) { try ImuFile.packets(b) }
    }
}

@Suite struct AnalysisTests {
    let a = analyzeSession(try! ImuFile.parse(sampleBytes()))

    @Test func sanityNumbers() {
        #expect(a.swings.count == 4)
        #expect(a.summary.hitCount == 3)
        #expect((a.summary.peakDps! * 10).rounded() / 10 == 2056.6)
        #expect(a.summary.saturatedGyro == 12)
    }

    @Test func gyroBias() {
        let b = a.fused!.bias / DEG
        #expect(abs(b.x - -1.820) < 0.005 && abs(b.y - -2.075) < 0.005 && abs(b.z - -1.108) < 0.005)
    }

    @Test func saturatedSwingIsFlagged() {
        let sat = a.swings.filter { $0.saturatedSamples > 0 }
        #expect(sat.map(\.index) == [3])
        #expect(sat[0].saturatedSamples == 12 && sat[0].confidence < 0.8)
    }

    @Test func clapsAndRallies() {
        #expect(abs(a.claps[0].t - 3.467) < 0.01)
        #expect(a.summary.rallyCount == 1)
        #expect(abs(a.summary.lossPct - 100 * 66 / 6600.0) < 1e-9)
    }

    @Test func liveAnalyzerFindsTheSameSwings() throws {
        let cap = try ImuFile.parse(sampleBytes())
        var live = LiveAnalyzer(rateHz: 500, mount: .racquet)
        var swings: [Swing] = []
        for chunk in stride(from: 0, to: cap.samples.count, by: 13) {
            swings += live.push(Array(cap.samples[chunk..<min(chunk + 13, cap.samples.count)]))
        }
        #expect(live.calibrated)
        #expect(swings.count == a.swings.count)
        for (l, o) in zip(swings, a.swings) { #expect(abs(l.tStart - o.tStart) < 0.003) }
    }
}

@Suite struct WristTests {
    /// 3 s at rest face up, then a 1 s "swing": rotation about z peaking at 900 dps, with a 10 g tap at its peak.
    func synthetic() -> [WristMotion] {
        (0..<(4 * 200)).map { i in
            let t = 100 + Double(i) / 200
            let u = t - 103
            let w = u > 0 && u < 0.4 ? 900 * DEG * sin(.pi * u / 0.4) : 0
            let tap = abs(u - 0.2) < 0.003 ? Vec3(10, 0, 0) : .zero
            return WristMotion(t: t, userAcc: tap, gravity: Vec3(0, 0, -1), rotationRate: Vec3(0, 0, w))
        }
    }

    @Test func encodesToThePodFormatWithWristMount() throws {
        var enc = WristEncoder()
        for m in synthetic() { enc.append(m) }
        let cap = try ImuFile.parse(enc.file())
        #expect(cap.header.rateHz == 200 && cap.header.mount == .wrist)
        #expect(cap.stats.unique == 800 && cap.stats.lost == 0)
        // Face up at rest: +1 g on z like the pod's MPU (Apple reports gravity z = -1)
        #expect(abs(cap.samples[0].acc.z / G - 1) < 0.001)
        #expect(abs(cap.samples[0].t) < 1e-9 && abs(cap.samples[1].t - 0.005) < 1e-6)
    }

    @Test func wristSessionAnalyzesAt200Hz() throws {
        var enc = WristEncoder()
        for m in synthetic() { enc.append(m) }
        let a = analyzeSession(try ImuFile.parse(enc.file()))
        #expect(a.mount == .wrist)
        #expect(a.fused?.window != nil) // 3 s still: calibrated even at 200 Hz
        #expect(a.swings.count == 1)
        #expect(abs(a.swings[0].peakDps - 900) < 5)
        #expect(a.swings[0].impact && a.swings[0].stroke == .unclassified)
        #expect(a.swings[0].overhead == nil)
    }
}

@Suite struct HeartRateTests {
    @Test func interpolatesAndAttaches() {
        let hr = HeartRateSeries([.init(t: 1000, bpm: 100), .init(t: 1002, bpm: 110), .init(t: 1030, bpm: 150)])
        #expect(hr.bpm(at: 1001) == 105)
        #expect(hr.bpm(at: 1020) == nil) // 28 s gap
        #expect(hr.bpm(at: 900) == nil)
        let anchor = ClockAnchor(deviceT: 3.0, wall: 1000)
        #expect(anchor.wall(4.0) == 1001 && anchor.device(1001) == 4.0)
        let a = analyzeSession(try! ImuFile.parse(sampleBytes()))
        let with = attachHeartRate(a.swings, hr, anchor: ClockAnchor(deviceT: a.swings[0].tStart, wall: 1001))
        #expect(with[0].heartRate == 105)
    }
}

@Suite struct GoogleHealthTests {
    @Test func rfc3339RoundTrip() {
        #expect(GoogleHealth.rfc3339(0) == "1970-01-01T00:00:00.000Z")
        #expect(GoogleHealth.rfc3339(1_792_198_924.123) == "2026-10-17T01:02:04.123Z")
        #expect(GoogleHealth.parseRFC3339("2026-10-17T01:02:04.123Z") == 1_792_198_924.123)
        #expect(GoogleHealth.parseRFC3339("2026-10-16T21:02:04-04:00") == 1_792_198_924)
        #expect(GoogleHealth.parseRFC3339("2024-02-29T12:00:00Z") == 1_709_208_000)
    }

    @Test func heartRateListURL() {
        let u = GoogleHealth.listURL(.heartRate, from: 1_792_198_924, to: 1_792_199_524)
        #expect(u.hasPrefix("https://health.googleapis.com/v4/users/me/dataTypes/heart-rate/dataPoints?filter="))
        #expect(u.contains("heart_rate.sample_time.physical_time%20%3E%3D%20%222026-10-17T01%3A02%3A04.000Z%22%20AND%20"))
        #expect(u.hasSuffix("&pageSize=10000"))
        #expect(GoogleHealth.listURL(.exercise, from: 0, to: 1).hasSuffix("&pageSize=25"))
    }

    @Test func authorizeURLCarriesPKCEAndScopes() {
        let u = GoogleHealth.authorizeURL(clientId: "abc.apps.googleusercontent.com", redirectURI: "com.oso:/oauth",
                                          codeChallenge: "xyz", state: "s1")
        #expect(u.contains("code_challenge_method=S256") && u.contains("redirect_uri=com.oso%3A%2Foauth"))
        #expect(u.contains("googlehealth.health_metrics_and_measurements.readonly"))
    }
}

@Suite struct UpdateThrottleTests {
    @Test func firstUpdateIsImmediateThenOnePerSecond() {
        var t = UpdateThrottle()
        #expect(t.request(at: 10) == 0)
        #expect(abs(t.request(at: 10.25) - 0.75) < 1e-9)   // too soon: wait for the rest of the second
        #expect(abs(t.request(at: 10.9) - 0.1) < 1e-9)
        #expect(t.request(at: 11) == 0)                      // the trailing update goes out and restarts the window
        #expect(abs(t.request(at: 11.5) - 0.5) < 1e-9)
    }

    @Test func neverMoreThanOnePerSecond() {
        var t = UpdateThrottle()
        var sent: [Double] = []
        var now = 0.0
        while now < 30 { if t.request(at: now) == 0 { sent.append(now) }; now += 0.1 }   // 10 requests per second
        #expect(sent.count <= 30)
        for (a, b) in zip(sent, sent.dropFirst()) { #expect(b - a >= 1 - 1e-9) }
    }

    @Test func resetMakesTheNextOneImmediate() {
        var t = UpdateThrottle()
        _ = t.request(at: 5)
        t.reset()
        #expect(t.request(at: 5.1) == 0)
    }
}
