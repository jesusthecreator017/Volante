// Weekly goal and streaks: this week's progress toward a target (sessions, minutes on court or swings), the run of weeks
// in a row that met it, and the snapshot the home-screen widget draws. Sample sessions never count.

import Foundation
import WidgetKit

enum GoalMetric: String, CaseIterable, Identifiable {
    case sessions, minutes, swings
    var id: String { rawValue }
    var title: String { switch self { case .sessions: "Sessions"; case .minutes: "Minutes on court"; case .swings: "Swings" } }
    var unit: String { switch self { case .sessions: "sessions"; case .minutes: "min"; case .swings: "swings" } }
    var defaultTarget: Int { switch self { case .sessions: 3; case .minutes: 90; case .swings: 1500 } }
    var step: Int { switch self { case .sessions: 1; case .minutes: 15; case .swings: 250 } }
    var range: ClosedRange<Int> { switch self { case .sessions: 1...14; case .minutes: 15...900; case .swings: 250...20000 } }
}

enum GoalKeys {
    static let metric = "volante.goal.metric"
    static let target = "volante.goal.target"
}

struct GoalProgress {
    var metric: GoalMetric
    var target: Int
    var value: Double
    var weekStart: Date
    var days: [Bool]
    var streak: Int
    var best: Int
    var fraction: Double { target > 0 ? value / Double(target) : 0 }
    var met: Bool { value >= Double(target) }

    static var saved: (GoalMetric, Int) {
        let d = UserDefaults.standard
        let m = GoalMetric(rawValue: d.string(forKey: GoalKeys.metric) ?? "") ?? .sessions
        let t = d.object(forKey: GoalKeys.target) as? Int ?? m.defaultTarget
        return (m, t)
    }

    @MainActor
    static func compute(_ store: SessionStore, metric: GoalMetric, target: Int, now: Date = Date(), calendar: Calendar = .current) -> GoalProgress {
        let rows = store.sessions.filter { $0.source != .sample }.compactMap { m in
            store.rowSummaries[m.id].flatMap { $0.readable ? (m.createdAt, $0) : nil }
        }
        func amount(_ r: SessionStore.RowSummary) -> Double {
            switch metric { case .sessions: 1; case .minutes: r.durationS / 60; case .swings: Double(r.swings) }
        }
        var perWeek: [Date: Double] = [:]
        for (date, r) in rows {
            if let w = calendar.dateInterval(of: .weekOfYear, for: date)?.start { perWeek[w, default: 0] += amount(r) }
        }
        let thisWeek = calendar.dateInterval(of: .weekOfYear, for: now) ?? DateInterval(start: now, duration: 7 * 86400)
        let value = perWeek[thisWeek.start] ?? 0
        let days = (0..<7).map { i -> Bool in
            guard let day = calendar.date(byAdding: .day, value: i, to: thisWeek.start) else { return false }
            return rows.contains { calendar.isDate($0.0, inSameDayAs: day) }
        }
        let goal = Double(target)
        func prev(_ w: Date) -> Date { calendar.date(byAdding: .weekOfYear, value: -1, to: w) ?? w }
        // The streak counts finished weeks that met the goal, plus this week once it has.
        var streak = 0, w = prev(thisWeek.start)
        while (perWeek[w] ?? 0) >= goal { streak += 1; w = prev(w) }
        if value >= goal { streak += 1 }
        var best = 0, run = 0
        if let first = perWeek.keys.min() {
            var x = first
            while x <= thisWeek.start {
                if (perWeek[x] ?? 0) >= goal { run += 1; best = max(best, run) } else if x < thisWeek.start { run = 0 }
                guard let n = calendar.date(byAdding: .weekOfYear, value: 1, to: x) else { break }
                x = n
            }
        }
        return GoalProgress(metric: metric, target: target, value: value, weekStart: thisWeek.start, days: days, streak: streak, best: max(best, streak))
    }

    /// Writes the widget's snapshot and reloads the widget when anything it shows changed.
    func publish() {
        let s = GoalSnapshot(metric: metric.rawValue, value: value.rounded(), target: Double(target), weekStart: weekStart,
                             days: days, streakWeeks: streak, bestStreak: best, updated: Date())
        var old = GoalSnapshot.load()
        old?.updated = s.updated
        guard old != s else { return }
        s.save()
        WidgetCenter.shared.reloadAllTimelines()
    }
}
