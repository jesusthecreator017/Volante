import Testing
@testable import OsoKit

@Suite struct PersonalBestsTests {
    typealias E = PersonalBests.Entry
    func e(_ id: String, sample: Bool = false, _ mount: Mount = .racquet, speed: Double? = 15, swings: Int = 10, rally: Int = 3) -> E {
        E(id: id, isSample: sample, mount: mount, maxSpeed: speed, swings: swings, longestRally: rally)
    }

    @Test func sampleSessionsNeverCount() {
        let b = PersonalBests([e("s", sample: true, speed: 30, swings: 99, rally: 50), e("a", speed: 12), e("b", speed: 14)])
        #expect(b.headSpeed == 14)
        #expect(b.mostSwings == 10)
        #expect(b.compared == 2)
        #expect(b.kinds(for: "s").isEmpty)
        #expect(b.kinds(for: "b").contains(.headSpeed))
        #expect(!b.kinds(for: "a").contains(.headSpeed))
    }

    @Test func wristSpeedIsSeparateFromRacquetSpeed() {
        let b = PersonalBests([e("r", speed: 12), e("w", .wrist, speed: 25)])
        #expect(b.headSpeed == 12)
        #expect(b.handSpeed == 25)
        #expect(b.kinds(for: "r").contains(.headSpeed))
        #expect(b.kinds(for: "w").contains(.handSpeed))
        #expect(!b.kinds(for: "w").contains(.headSpeed))
        #expect(b.context(for: .racquet) == "Best: 12.0 m/s")
        #expect(b.context(for: .wrist) == "Best: 25.0 m/s")
    }

    @Test func needsTwoSessionsToBadge() {
        #expect(PersonalBests([e("only")]).kinds(for: "only").isEmpty)
        #expect(PersonalBests([e("only"), e("x", sample: true)]).kinds(for: "only").isEmpty)
        #expect(!PersonalBests([e("a"), e("b")]).kinds(for: "a").isEmpty) // a tie holds the best
        #expect(PersonalBests().context(for: .racquet) == nil)
    }

    @Test func swingsAndRally() {
        let b = PersonalBests([e("a", swings: 40, rally: 2), e("b", swings: 20, rally: 9)])
        #expect(b.kinds(for: "a").contains(.swings))
        #expect(b.kinds(for: "b").contains(.rally))
        #expect(!b.kinds(for: "a").contains(.rally))
    }
}

@Suite struct SwingTraceTests {
    let a = analyzeSession(try! ImuFile.parse(sampleBytes()))

    @Test func forwardPeakLinesUpWithImpactTiming() throws {
        for s in a.swings where s.impact {
            let t = SwingTrace(a, s)
            let d = try #require(s.impactVsPeakMs)
            // timing is measured against the forward peak: impact (0 ms) minus peak = d
            #expect(abs(-t.fwdPeakMs - d) < 1.5)
            #expect(t.fwdPeakDps == s.fwdPeakDps)
            #expect(t.maxDps == s.peakDps)
            #expect(t.impactMs == 0)
        }
    }

    @Test func backswingMaxIsFlaggedSeparately() throws {
        let s = try #require(a.swings.first { $0.index == 3 })
        let t = SwingTrace(a, s)
        #expect(t.showsMax)
        #expect(t.maxInBackswing)
        #expect(t.maxFromBackswing)
        #expect(t.maxDps > t.fwdPeakDps)
        #expect(t.maxMs < -300 && abs(t.fwdPeakMs) < 20)
    }

    @Test func bandsAreOrderedAndInsideTheWindow() throws {
        for s in a.swings {
            let t = SwingTrace(a, s)
            #expect(!t.points.isEmpty)
            var prev = -Double.infinity
            for b in t.bands {
                #expect(b.from >= prev - 0.001 && b.to >= b.from && b.from >= t.xMin && b.to <= t.xMax)
                prev = b.to
            }
        }
        let s = try #require(a.swings.first { $0.saturatedSamples > 0 })
        #expect(!SwingTrace(a, s).clips.isEmpty)
    }
}
