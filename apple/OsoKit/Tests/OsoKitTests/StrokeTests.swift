import Foundation
import Testing
@testable import OsoKit

/// models/strokes/<name> from the repo (the single source; the bundled copies must equal it).
func repoModelData(_ name: String) throws -> Data {
    let here = "\(#filePath)"
    let root = here.ranges(of: "/apple/OsoKit/").first.map { String(here[..<$0.lowerBound]) } ?? "."
    return try Data(contentsOf: URL(fileURLWithPath: root + "/models/strokes/" + name))
}

struct Vectors: Decodable {
    struct Vec: Decodable { let w, a: [Double]; let sat_gyro, sat_acc: [Int]; let features: [Double]; let group, cls, side: [Double] }
    struct Fixture: Decodable {
        let packets: [[Int]]; let t_peak_us: Int; let flags: [String]; let w, a: [Double]; let sat_gyro, sat_acc: [Int]
    }
    let features_version: Int
    let group_classes, class_classes: [String]
    let vectors: [Vec]
    let window_fixture: Fixture

    static func load() throws -> Vectors { try JSONDecoder().decode(Vectors.self, from: repoModelData("test_vectors_trees_v0.json")) }
}

extension Vectors.Vec {
    var window: StrokeWindowData {
        func flags(_ idx: [Int]) -> [Double] { var f = [Double](repeating: 0, count: 500); for i in idx { f[i] = 1 }; return f }
        return StrokeWindowData(w: w, a: a, satGyro: flags(sat_gyro), satAcc: flags(sat_acc))
    }
}

func close(_ a: Double, _ b: Double, abs aTol: Double, rel: Double) -> Bool { Swift.abs(a - b) <= aTol + rel * Swift.abs(b) }

@Suite struct StrokeModelTests {
    @Test func bundledModelsAreTheRepoModels() throws {
        for name in ["stroke_group_v0.trees", "stroke_class_v0.trees"] {
            let url = try #require(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Resources")
                ?? Bundle.module.url(forResource: name, withExtension: "json"))
            #expect(try Data(contentsOf: url) == repoModelData(name + ".json"), "\(name): run apple/scripts/sync-models.sh")
        }
        #expect(StrokeClassifier.shared != nil)
    }

    @Test func featuresMatchThePythonVectors() throws {
        let v = try Vectors.load()
        #expect(v.features_version == StrokeFeatures.version && v.vectors.count == 20)
        let model = try #require(StrokeClassifier.shared).group
        var worst = 0.0
        for (k, e) in v.vectors.enumerated() {
            let f = StrokeFeatures.compute(e.window)
            for (i, name) in model.featureNames.enumerated() {
                let got = try #require(f[name]), want = e.features[i]
                worst = max(worst, Swift.abs(got - want) / (1e-4 + 1e-5 * Swift.abs(want)))
                #expect(close(got, want, abs: 1e-4, rel: 1e-5), "vector \(k) \(name): \(got) vs \(want)")
            }
        }
        #expect(worst <= 1)
    }

    @Test func probabilitiesMatchThePythonVectors() throws {
        let v = try Vectors.load()
        let c = try #require(StrokeClassifier.shared)
        #expect(c.group.head.classes == v.group_classes && c.klass.head.classes == v.class_classes)
        for e in v.vectors {
            // trees alone, on the exact expected features
            let x = e.features.map { Float($0) }
            for (got, want) in [(c.group.head.predict(x), e.group), (c.klass.head.predict(x), e.cls), (c.group.side!.predict(x), e.side)] {
                #expect(got.count == want.count)
                for (g, w) in zip(got, want) { #expect(Swift.abs(g - w) < 1e-5) }
            }
            // the whole path: window -> features -> trees
            let f = StrokeFeatures.compute(e.window)
            for (got, want) in [(c.group.head.predict(c.group.vector(f)), e.group), (c.klass.head.predict(c.klass.vector(f)), e.cls)] {
                for (g, w) in zip(got, want) { #expect(Swift.abs(g - w) < 1e-5) }
            }
        }
    }

    @Test func refusesAMismatchedFeaturesVersionOrSchema() throws {
        let raw = String(decoding: try repoModelData("stroke_group_v0.trees.json"), as: UTF8.self)
        let v1 = raw.replacingOccurrences(of: "\"features_version\":2", with: "\"features_version\":3")
        #expect(v1 != raw)
        #expect(throws: TreeModelError.featuresVersion(model: 3, app: 2)) { try TreeModel(json: Data(v1.utf8)) }
        let s = raw.replacingOccurrences(of: "volante-trees/1", with: "volante-trees/2")
        #expect(throws: TreeModelError.schema("volante-trees/2")) { try TreeModel(json: Data(s.utf8)) }
        let n = raw.replacingOccurrences(of: "\"w_peak_dps\"", with: "\"w_peak_bogus\"")
        #expect(throws: TreeModelError.unknownFeature("w_peak_bogus")) { try TreeModel(json: Data(n.utf8)) }
        #expect(throws: (any Error).self) { try TreeModel(json: Data("{}".utf8)) }
    }

    @Test func aTinyHandWrittenForestEvaluates() throws {
        // one softmax output pair, one tree: x0 <= 1 -> leaf 0.5 else leaf -0.5; the second output's tree is the mirror
        let tree = { (a: Double, b: Double) in "{\"f\":[0,-1,-1],\"v\":[1.0,\(a),\(b)],\"r\":[2,0,0],\"m\":\"100\"}" }
        let json = """
        {"schema":"volante-trees/1","name":"t","version":"v0","created":"2026-01-01","training_data_sha256":"0","features_version":2,
         "feature_names":["w_peak_dps"],"display_threshold":{"threshold":0.6,"coverage":1,"accuracy":0.9,"met":true,"min_accuracy":0.8},
         "classes":["a","b"],"link":"softmax","baseline":[0,0],"per_iter":2,"trees":[\(tree(0.5, -0.5)),\(tree(-0.5, 0.5))]}
        """
        let m = try TreeModel(json: Data(json.utf8))
        let lo = m.head.predict([0.5]), hi = m.head.predict([2])
        #expect(Swift.abs(lo[0] - 1 / (1 + exp(-1.0))) < 1e-12 && Swift.abs(lo.reduce(0, +) - 1) < 1e-12)
        #expect(hi[1] > hi[0])
        #expect(m.head.predict([1])[0] > 0.5) // x <= threshold goes left
    }
}

@Suite struct StrokeWindowTests {
    @Test func matchesThePythonWindowOnAnImuFixture() throws {
        let fx = try Vectors.load().window_fixture
        let packets = fx.packets.map { RawPacket(seq16: UInt16($0[0]), t32: UInt32($0[1]), v: $0[2...].map { Int16($0) }) }
        let cap = ImuFile.assemble(header: ImuHeader(rateHz: 500, mountId: 0), packets: packets)
        let win = try #require(StrokeWindowData.build(cap.samples, tPeak: Double(fx.t_peak_us) * 1e-6))
        #expect(win.hole == fx.flags.contains("hole") && win.edge == fx.flags.contains("edge") && win.hole)
        var worst = 0.0
        for k in 0..<500 {
            worst = max(worst, Swift.abs(win.w[k] - fx.w[k]) / (1e-3 + 1e-5 * Swift.abs(fx.w[k])), Swift.abs(win.a[k] - fx.a[k]) / (1e-4 + 1e-5 * Swift.abs(fx.a[k])))
        }
        #expect(worst <= 1, "window differs from Python by \(worst) tolerances")
        #expect((0..<500).filter { win.satGyro[$0] > 0 } == fx.sat_gyro && (0..<500).filter { win.satAcc[$0] > 0 } == fx.sat_acc)
        #expect(!fx.sat_gyro.isEmpty)
    }

    @Test func flagsWindowsThatLeaveTheRecording() throws {
        let cap = try ImuFile.parse(sampleBytes())
        let w = try #require(StrokeWindowData.build(cap.samples, tPeak: cap.samples[0].t + 0.2))
        #expect(w.edge)
        #expect(StrokeClassifier.shared?.classify(w) == StrokeResult.unclassified)
    }

    /// tools/strokes imu.py `onsets` + features.py `peak_index`/`dedupe` on app/public/sample/imu.bin (device clock, us)
    static let pythonPeaksUs: [Double] = [4_147_996, 5_970_000, 8_116_014, 10_138_014]

    @Test func appSwingPeaksAreThePythonPeaksOnTheSampleSession() throws {
        let a = analyzeSession(try ImuFile.parse(sampleBytes()))
        #expect(a.swings.count == Self.pythonPeaksUs.count)
        for (s, p) in zip(a.swings, Self.pythonPeaksUs) { #expect(Swift.abs(s.tPeak * 1e6 - p) <= 2000, "swing \(s.index): \(s.tPeak * 1e6) vs \(p)") }
        let c = try #require(StrokeClassifier.shared)
        let r = c.classify(a)
        #expect(r.count == a.swings.count)
        #expect(r == c.classify(a)) // deterministic
        let b = StrokeBreakdown(swings: a.swings, results: r)
        #expect(b.total == r.count && b.groups.values.reduce(0) { $0 + $1.count } == r.count)
        var m = b
        m.merge(b)
        #expect(m.total == 2 * b.total && m.fh == 2 * b.fh)
    }
}

@Suite struct StrokeBreakdownTests {
    func swing(_ i: Int, _ speed: Double) -> Swing {
        var s = analyzeSession(try! ImuFile.parse(sampleBytes())).swings[0]
        s.index = i; s.headSpeed = speed
        return s
    }

    @Test func countsGroupsSidesAndGuesses() {
        let r = [
            StrokeResult(group: .power, groupConfidence: 0.9, stroke: "smash", strokeConfidence: 0.9, side: .fh),
            StrokeResult(group: .power, groupConfidence: 0.8, stroke: "smash", strokeConfidence: 0.9, side: .bh),
            StrokeResult(group: .touch, groupConfidence: 0.8, stroke: "net_shot", strokeConfidence: 0.9, side: nil),
            .unclassified,
        ]
        let b = StrokeBreakdown(swings: [swing(1, 20), swing(2, 10), swing(3, 4), swing(4, 6)], results: r)
        #expect(b.total == 4 && b.stat(.power).count == 2 && b.stat(.power).meanSpeed == 15 && b.stat(.unclassified).count == 1)
        #expect(b.fh == 1 && b.bh == 1)
        #expect(b.topGuesses.map(\.stroke) == ["smash", "net_shot"] && b.topGuesses[0].count == 2)
        #expect(b.rows.map(\.group) == [.power, .touch, .unclassified])
        let again = try? JSONDecoder().decode(StrokeBreakdown.self, from: JSONEncoder().encode(b))
        #expect(again == b)
        #expect(StrokeResult.title("net_shot") == "Net shot")
    }
}
