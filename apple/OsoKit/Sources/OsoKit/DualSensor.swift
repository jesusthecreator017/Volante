// Pod + Apple Watch in one session: the racquet pod measures the head, the watch the wrist. Both see the same swing a few
// milliseconds apart (the wrist leads the head), so matching their swing times on the wall clock tells which pod swings
// were real strokes (both moved) and which were the racquet alone (a bounce, picking it up), and gives the session the
// watch's heart rate. Greedy one-to-one matching in time order; both lists are sorted.

public struct DualSensorMatch: Equatable, Sendable {
    /// pod swing index -> matched wrist swing index
    public var pairs: [(pod: Int, wrist: Int)]
    public var podOnly: [Int]
    public var wristOnly: [Int]
    /// share of pod swings the wrist confirmed (nil without pod swings)
    public var confirmedShare: Double? { pairs.count + podOnly.count == 0 ? nil : Double(pairs.count) / Double(pairs.count + podOnly.count) }

    public static func == (a: Self, b: Self) -> Bool {
        a.pairs.map(\.pod) == b.pairs.map(\.pod) && a.pairs.map(\.wrist) == b.pairs.map(\.wrist) && a.podOnly == b.podOnly && a.wristOnly == b.wristOnly
    }

    /// `pod` / `wrist`: swing start times in wall-clock seconds. `tolerance`: how far apart the two starts may be.
    public static func match(pod: [Double], wrist: [Double], tolerance: Double = 0.25) -> DualSensorMatch {
        var pairs: [(Int, Int)] = [], podOnly: [Int] = [], used = Set<Int>()
        var j = 0
        for (i, t) in pod.enumerated() {
            while j < wrist.count, wrist[j] < t - tolerance { j += 1 }
            var best: Int?
            var k = j
            while k < wrist.count, wrist[k] <= t + tolerance {
                if !used.contains(k), best == nil || abs(wrist[k] - t) < abs(wrist[best!] - t) { best = k }
                k += 1
            }
            if let b = best { pairs.append((i, b)); used.insert(b) } else { podOnly.append(i) }
        }
        let wristOnly = wrist.indices.filter { !used.contains($0) }
        return DualSensorMatch(pairs: pairs.map { (pod: $0.0, wrist: $0.1) }, podOnly: podOnly, wristOnly: wristOnly)
    }
}
