// On-device stroke models (Phase 22): the intensity group (power / mid / touch), a FH/BH side and an experimental 10-stroke guess,
// all gradient-boosted trees on the 24 orientation-free features of one swing window. Models: Resources/*.trees.json, copies of
// models/strokes/ (apple/scripts/sync-models.sh). Trained on one player's drill session: experimental, see docs/strokes/model-card.md.

import Foundation

public enum StrokeGroup: String, Codable, Sendable, CaseIterable {
    case power, mid, touch
    case notAHit = "not_a_hit"
    case unclassified

    public var title: String { self == .notAHit ? "Not a hit" : rawValue.capitalized }
}

public enum StrokeSide: String, Codable, Sendable { case fh = "FH", bh = "BH" }

public struct StrokeResult: Codable, Equatable, Sendable {
    public var group: StrokeGroup
    public var groupConfidence: Double
    /// The 10-stroke guess (model class name such as "smash", "net_shot"); nil = unclassified (below the display threshold).
    public var stroke: String?
    public var strokeConfidence: Double
    /// Only when the side head is confident (> 0.6) and the group is not "not_a_hit".
    public var side: StrokeSide?

    public init(group: StrokeGroup, groupConfidence: Double, stroke: String?, strokeConfidence: Double, side: StrokeSide?) {
        self.group = group; self.groupConfidence = groupConfidence; self.stroke = stroke; self.strokeConfidence = strokeConfidence; self.side = side
    }

    public static let unclassified = StrokeResult(group: .unclassified, groupConfidence: 0, stroke: nil, strokeConfidence: 0, side: nil)

    /// "net_shot" -> "Net shot"
    public static func title(_ stroke: String) -> String { stroke.replacingOccurrences(of: "_", with: " ").capitalized(first: true) }
    public var strokeTitle: String? { stroke.map(Self.title) }
}

private extension String {
    func capitalized(first: Bool) -> String { prefix(1).uppercased() + dropFirst() }
}

public final class StrokeClassifier: Sendable {
    public static let sideConfidence = 0.6

    public let group: TreeModel
    public let klass: TreeModel

    public init(group: Data, klass: Data) throws {
        self.group = try TreeModel(json: group)
        self.klass = try TreeModel(json: klass)
        guard self.group.side != nil else { throw TreeModelError.malformed("group model lacks the side head") }
    }

    /// The models bundled with OsoKit.
    public static func bundled() throws -> StrokeClassifier {
        func load(_ name: String) throws -> Data {
            guard let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Resources") ?? Bundle.module.url(forResource: name, withExtension: "json")
            else { throw TreeModelError.malformed("missing resource \(name)") }
            return try Data(contentsOf: url)
        }
        return try StrokeClassifier(group: load("stroke_group_v0.trees"), klass: load("stroke_class_v0.trees"))
    }

    /// nil only if the bundle is broken (a test guards that).
    public static let shared: StrokeClassifier? = try? bundled()

    /// Changes whenever a model file does, so cached results can be invalidated.
    public var modelVersion: String {
        "\(group.version)-\(group.trainingDataSha.prefix(8))-\(group.created)/\(klass.version)-\(klass.trainingDataSha.prefix(8))-\(klass.created)"
    }

    public func classify(_ win: StrokeWindowData) -> StrokeResult {
        // A window that leaves the recording or spans a long gap is not what the models were trained on
        if win.edge || win.hole { return .unclassified }
        let f = StrokeFeatures.compute(win)
        let pg = group.head.predict(group.vector(f))
        let gi = pg.indices.max { pg[$0] < pg[$1] }!
        let gName = group.head.classes[gi]
        var r = StrokeResult.unclassified
        r.groupConfidence = pg[gi]
        if pg[gi] >= group.display.threshold { r.group = StrokeGroup(rawValue: gName) ?? .unclassified }
        if let side = group.side, gName != StrokeGroup.notAHit.rawValue {
            let ps = side.predict(group.vector(f))
            if let m = ps.max(), m > Self.sideConfidence { r.side = ps[1] > ps[0] ? .bh : .fh }
        }
        let pc = klass.head.predict(klass.vector(f))
        let ci = pc.indices.max { pc[$0] < pc[$1] }!
        r.strokeConfidence = pc[ci]
        if pc[ci] >= klass.display.threshold { r.stroke = klass.head.classes[ci] }
        return r
    }

    public func classify(samples: [Sample], tPeak: Double) -> StrokeResult {
        StrokeWindowData.build(samples, tPeak: tPeak).map(classify) ?? .unclassified
    }

    /// One result per swing, in order. Racquet sessions only: the models read the racquet pod's gyro and accelerometer.
    public func classify(_ a: SessionAnalysis) -> [StrokeResult] {
        guard a.mount == .racquet else { return [] }
        return a.swings.map { classify(samples: a.capture.samples, tPeak: $0.tPeak) }
    }
}

/// What a session (or several) looked like by group: counts and mean head speed, the FH/BH split of classified swings and the
/// experimental 10-stroke guesses.
public struct StrokeBreakdown: Codable, Equatable, Sendable {
    public struct Stat: Codable, Equatable, Sendable {
        public var count = 0
        public var speedSum = 0.0
        public var meanSpeed: Double? { count > 0 ? speedSum / Double(count) : nil }
    }

    /// keyed by StrokeGroup.rawValue (a Codable dictionary with enum keys would encode as an array)
    public var groups: [String: Stat] = [:]
    public var fh = 0, bh = 0
    public var guesses: [String: Int] = [:]
    public var total = 0

    public init() {}

    public init(swings: [Swing], results: [StrokeResult]) {
        for (s, r) in zip(swings, results) {
            total += 1
            groups[r.group.rawValue, default: Stat()].count += 1
            groups[r.group.rawValue, default: Stat()].speedSum += s.headSpeed
            if r.group != .unclassified { if r.side == .fh { fh += 1 } else if r.side == .bh { bh += 1 } }
            if let k = r.stroke { guesses[k, default: 0] += 1 }
        }
    }

    public func stat(_ g: StrokeGroup) -> Stat { groups[g.rawValue] ?? Stat() }

    /// Group rows in display order (power, mid, touch, not a hit, unclassified), empty ones left out.
    public var rows: [(group: StrokeGroup, stat: Stat)] {
        [StrokeGroup.power, .mid, .touch, .notAHit, .unclassified].compactMap { g in stat(g).count > 0 ? (g, stat(g)) : nil }
    }

    /// Guesses, most frequent first.
    public var topGuesses: [(stroke: String, count: Int)] {
        guesses.map { ($0.key, $0.value) }.sorted { ($1.1, $0.0) < ($0.1, $1.0) }
    }

    public mutating func merge(_ o: StrokeBreakdown) {
        for (k, v) in o.groups {
            groups[k, default: Stat()].count += v.count
            groups[k, default: Stat()].speedSum += v.speedSum
        }
        for (k, v) in o.guesses { guesses[k, default: 0] += v }
        fh += o.fh; bh += o.bh; total += o.total
    }
}

/// What the phone caches per session: the results (aligned with the analysis' swings) and the model version that made them.
public struct SessionStrokes: Codable, Equatable, Sendable {
    public var modelVersion: String
    public var results: [StrokeResult]
    public init(modelVersion: String, results: [StrokeResult]) { self.modelVersion = modelVersion; self.results = results }

    /// Valid for this analysis: same model and one result per swing (a changed detector threshold changes the count).
    public func valid(for a: SessionAnalysis, classifier: StrokeClassifier) -> Bool {
        modelVersion == classifier.modelVersion && results.count == a.swings.count
    }
}
