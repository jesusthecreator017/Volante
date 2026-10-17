// The weekly goal as the home-screen widget sees it: the app writes it to the shared App Group, the widget reads it.

import Foundation

struct GoalSnapshot: Codable, Equatable {
    /// "sessions", "minutes" or "swings"
    var metric: String
    var value: Double
    var target: Double
    /// start of the week the value counts
    var weekStart: Date
    /// days played this week, Monday first (7 entries, in the user's calendar order)
    var days: [Bool]
    var streakWeeks: Int
    var bestStreak: Int
    var updated: Date

    var unit: String { metric == "minutes" ? "min" : metric }
    var fraction: Double { target > 0 ? value / target : 0 }
    var met: Bool { value >= target }

    /// The week rolled over since the app last wrote: nothing counted yet this week.
    func current(now: Date = Date(), calendar: Calendar = .current) -> GoalSnapshot {
        guard let week = calendar.dateInterval(of: .weekOfYear, for: now), week.start > weekStart else { return self }
        var s = self
        let lastWeekMet = met && calendar.dateInterval(of: .weekOfYear, for: week.start.addingTimeInterval(-1))?.start == weekStart
        s.value = 0
        s.days = Array(repeating: false, count: 7)
        s.weekStart = week.start
        if !lastWeekMet { s.streakWeeks = 0 }
        return s
    }

    static let placeholder = GoalSnapshot(metric: "sessions", value: 2, target: 3, weekStart: Date(),
                                          days: [true, false, true, false, false, false, false], streakWeeks: 3, bestStreak: 5, updated: Date())

    private static let key = "volante.goal.snapshot"
    static var defaults: UserDefaults? {
        (Bundle.main.object(forInfoDictionaryKey: "VolanteAppGroup") as? String).flatMap { UserDefaults(suiteName: $0) }
    }
    static func load() -> GoalSnapshot? {
        defaults?.data(forKey: key).flatMap { try? JSONDecoder().decode(GoalSnapshot.self, from: $0) }
    }
    func save() { if let d = try? JSONEncoder().encode(self) { Self.defaults?.set(d, forKey: Self.key) } }
}
