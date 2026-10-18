// Gradient-boosted tree ensembles exported by tools/strokes/export_trees.py (schema `volante-trees/1`, docs/strokes/model-card.md).
// A tree is stored in preorder (left child = next node): f feature index (-1 = leaf), v threshold or leaf value, r right child,
// m missing-goes-left bits. Go left when x <= threshold, x and threshold float32. Raw score = baseline + sum of leaf values per
// output, then softmax (or sigmoid for one output).

import Foundation

public enum TreeModelError: Error, Equatable {
    case schema(String)
    case featuresVersion(model: Int, app: Int)
    case unknownFeature(String)
    case malformed(String)
}

/// One output head: trees `i` belong to output `i % perIter`.
public struct TreeHead: Sendable {
    public let classes: [String]
    public let perIter: Int
    let sigmoid: Bool
    let baseline: [Double]
    /// Flat node arrays over all trees; `roots[t]` is the first node of tree t.
    let roots: [Int32]
    let feature: [Int16]
    let threshold: [Float]
    let leaf: [Double]
    let right: [Int32]
    let missingLeft: [Bool]

    fileprivate init(_ d: TreeModel.Dto.Head) throws {
        guard d.per_iter > 0, d.baseline.count == d.per_iter, d.trees.count % d.per_iter == 0 else { throw TreeModelError.malformed("head shape") }
        guard d.link == "softmax" || (d.link == "sigmoid" && d.per_iter == 1) else { throw TreeModelError.malformed("link \(d.link)") }
        classes = d.classes; perIter = d.per_iter; sigmoid = d.link == "sigmoid"; baseline = d.baseline
        var roots: [Int32] = [], feature: [Int16] = [], threshold: [Float] = [], leaf: [Double] = [], right: [Int32] = [], ml: [Bool] = []
        for t in d.trees {
            let n = t.f.count
            guard n > 0, t.v.count == n, t.r.count == n, t.m.utf8.count == n, t.f[n - 1] == -1 else { throw TreeModelError.malformed("tree") }
            let base = Int32(feature.count)
            roots.append(base)
            for i in 0..<n {
                feature.append(Int16(t.f[i]))
                threshold.append(Float(t.v[i]))
                leaf.append(t.v[i])
                right.append(base + Int32(t.r[i]))
                if t.f[i] >= 0 && !(t.r[i] > i + 1 && t.r[i] < n) { throw TreeModelError.malformed("child index") }
            }
            ml += t.m.utf8.map { $0 == 0x31 }
        }
        self.roots = roots; self.feature = feature; self.threshold = threshold; self.leaf = leaf; self.right = right; missingLeft = ml
        if classes.count != (sigmoid ? 2 : perIter) { throw TreeModelError.malformed("classes") }
    }

    public var maxFeatureIndex: Int { Int(feature.max() ?? -1) }

    /// Probabilities over `classes` for one float32 feature vector.
    public func predict(_ x: [Float]) -> [Double] {
        var raw = baseline
        for (t, root) in roots.enumerated() {
            var i = Int(root)
            while feature[i] >= 0 {
                let v = x[Int(feature[i])]
                i = (v.isNaN ? missingLeft[i] : v <= threshold[i]) ? i + 1 : Int(right[i])
            }
            raw[t % perIter] += leaf[i]
        }
        if sigmoid {
            let p = 1 / (1 + exp(-raw[0]))
            return [1 - p, p]
        }
        let mx = raw.max() ?? 0
        let e = raw.map { exp($0 - mx) }
        let s = e.reduce(0, +)
        return e.map { $0 / s }
    }
}

public struct DisplayThreshold: Sendable, Equatable {
    /// Below this max probability the app says "Unclassified".
    public var threshold: Double
    public var coverage: Double
    public var accuracy: Double?
    /// whether the out-of-fold accuracy target (`minAccuracy`) was reached at all
    public var met: Bool
    public var minAccuracy: Double
}

public struct TreeModel: Sendable {
    public let name: String
    public let version: String
    public let created: String
    public let trainingDataSha: String
    public let featureNames: [String]
    public let head: TreeHead
    public let side: TreeHead?
    public let display: DisplayThreshold
    public let classToGroup: [String: String]?

    public init(json: Data) throws {
        let d: Dto
        do { d = try JSONDecoder().decode(Dto.self, from: json) } catch { throw TreeModelError.malformed("\(error)") }
        guard d.schema == "volante-trees/1" else { throw TreeModelError.schema(d.schema) }
        guard d.features_version == StrokeFeatures.version else {
            throw TreeModelError.featuresVersion(model: d.features_version, app: StrokeFeatures.version)
        }
        for n in d.feature_names where !StrokeFeatures.names.contains(n) { throw TreeModelError.unknownFeature(n) }
        name = d.name; version = d.version; created = d.created; trainingDataSha = d.training_data_sha256
        featureNames = d.feature_names
        head = try TreeHead(d.head)
        side = try d.side.map { try TreeHead($0) }
        display = DisplayThreshold(
            threshold: d.display_threshold.threshold, coverage: d.display_threshold.coverage, accuracy: d.display_threshold.accuracy,
            met: d.display_threshold.met, minAccuracy: d.display_threshold.min_accuracy)
        classToGroup = d.class_to_group
        guard head.maxFeatureIndex < featureNames.count, (side?.maxFeatureIndex ?? -1) < featureNames.count else {
            throw TreeModelError.malformed("feature index")
        }
    }

    /// Features in this model's order from a full feature set.
    public func vector(_ f: [String: Double]) -> [Float] { featureNames.map { Float(f[$0] ?? 0) } }

    struct Dto: Decodable {
        struct Tree: Decodable { let f: [Int]; let v: [Double]; let r: [Int]; let m: String }
        struct Head: Decodable { let classes: [String]; let link: String; let baseline: [Double]; let per_iter: Int; let trees: [Tree] }
        struct Display: Decodable { let threshold: Double; let coverage: Double; let accuracy: Double?; let met: Bool; let min_accuracy: Double }
        let schema: String, name: String, version: String, created: String, training_data_sha256: String
        let features_version: Int
        let feature_names: [String]
        let display_threshold: Display
        let class_to_group: [String: String]?
        let side: Head?
        let head: Head

        enum CodingKeys: String, CodingKey {
            case schema, name, version, created, training_data_sha256, features_version, feature_names, display_threshold, class_to_group, side
            case classes, link, baseline, per_iter, trees
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            schema = try c.decode(String.self, forKey: .schema)
            name = try c.decode(String.self, forKey: .name)
            version = try c.decode(String.self, forKey: .version)
            created = try c.decode(String.self, forKey: .created)
            training_data_sha256 = try c.decode(String.self, forKey: .training_data_sha256)
            features_version = try c.decode(Int.self, forKey: .features_version)
            feature_names = try c.decode([String].self, forKey: .feature_names)
            display_threshold = try c.decode(Display.self, forKey: .display_threshold)
            class_to_group = try c.decodeIfPresent([String: String].self, forKey: .class_to_group)
            side = try c.decodeIfPresent(Head.self, forKey: .side)
            // the head's fields sit at the top level of the file
            head = Head(classes: try c.decode([String].self, forKey: .classes), link: try c.decode(String.self, forKey: .link),
                        baseline: try c.decode([Double].self, forKey: .baseline), per_iter: try c.decode(Int.self, forKey: .per_iter),
                        trees: try c.decode([Tree].self, forKey: .trees))
        }
    }
}
