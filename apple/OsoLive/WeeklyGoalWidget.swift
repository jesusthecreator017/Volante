// Home-screen and Lock Screen widget: this week's goal ring, days played and the streak, from the snapshot the app shares.

import SwiftUI
import WidgetKit

struct GoalEntry: TimelineEntry {
    let date: Date
    let goal: GoalSnapshot?
}

struct GoalProvider: TimelineProvider {
    func placeholder(in context: Context) -> GoalEntry { GoalEntry(date: Date(), goal: .placeholder) }
    func getSnapshot(in context: Context, completion: @escaping (GoalEntry) -> Void) {
        completion(GoalEntry(date: Date(), goal: context.isPreview ? (GoalSnapshot.load() ?? .placeholder) : GoalSnapshot.load()))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<GoalEntry>) -> Void) {
        // The app reloads the widget whenever the goal changes; refresh at midnight too, so a new week starts at zero.
        let now = Date(), cal = Calendar.current
        let midnight = cal.nextDate(after: now, matching: DateComponents(hour: 0, minute: 1), matchingPolicy: .nextTime) ?? now.addingTimeInterval(3600)
        completion(Timeline(entries: [GoalEntry(date: now, goal: GoalSnapshot.load()?.current(now: now))], policy: .after(midnight)))
    }
}

struct WeeklyGoalWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "WeeklyGoal", provider: GoalProvider()) { entry in
            GoalWidgetView(entry: entry).containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Weekly goal")
        .description("Your badminton goal for the week and your streak.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular])
    }
}

struct GoalWidgetView: View {
    let entry: GoalEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        if let g = entry.goal {
            switch family {
            case .accessoryCircular:
                Gauge(value: min(1, g.fraction)) { Image(systemName: "figure.badminton") } currentValueLabel: { Text(n(g.value)) }
                    .gaugeStyle(.accessoryCircular)
            case .accessoryRectangular:
                VStack(alignment: .leading, spacing: 2) {
                    Label("Volante", systemImage: "figure.badminton").font(.headline).widgetAccentable()
                    Text("\(n(g.value)) of \(n(g.target)) \(g.unit)")
                    Text(g.streakWeeks > 0 ? "\(g.streakWeeks)-week streak" : "No streak yet").foregroundStyle(.secondary)
                }
            case .systemMedium:
                HStack(spacing: 16) {
                    ring(g).frame(width: 110, height: 110)
                    VStack(alignment: .leading, spacing: 8) {
                        Text("This week").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                        Text(g.met ? "Goal reached" : "\(n(max(0, g.target - g.value))) \(g.unit) to go").font(.headline)
                        days(g)
                        streak(g)
                    }
                    Spacer(minLength: 0)
                }
            default:
                VStack(alignment: .leading, spacing: 6) {
                    ring(g).frame(width: 78, height: 78)
                    Spacer(minLength: 0)
                    streak(g)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            VStack(spacing: 6) {
                Image(systemName: "figure.badminton").font(.title2).foregroundStyle(.tint)
                Text("Open Volante to set a weekly goal").font(.caption).multilineTextAlignment(.center)
            }
        }
    }

    private func ring(_ g: GoalSnapshot) -> some View {
        ZStack {
            Circle().stroke(Color.accentColor.opacity(0.2), lineWidth: 12)
            Circle().trim(from: 0, to: min(1, max(0.001, g.fraction)))
                .stroke(Color.accentColor.gradient, style: StrokeStyle(lineWidth: 12, lineCap: .round))
                .rotationEffect(.degrees(-90))
            VStack(spacing: 0) {
                Text(n(g.value)).font(.system(.title3, design: .rounded, weight: .heavy)).monospacedDigit()
                Text("of \(n(g.target))").font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private func days(_ g: GoalSnapshot) -> some View {
        let symbols = Calendar.current.veryShortStandaloneWeekdaySymbols, first = Calendar.current.firstWeekday - 1
        return HStack(spacing: 5) {
            ForEach(0..<7, id: \.self) { i in
                VStack(spacing: 2) {
                    Circle().fill(g.days.indices.contains(i) && g.days[i] ? Color.accentColor : Color.secondary.opacity(0.25)).frame(width: 9, height: 9)
                    Text(symbols[(first + i) % 7]).font(.system(size: 8)).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func streak(_ g: GoalSnapshot) -> some View {
        Label(g.streakWeeks > 0 ? "\(g.streakWeeks) wk" : "Start", systemImage: "flame.fill")
            .font(.caption.weight(.semibold))
            .foregroundStyle(g.streakWeeks > 0 ? Color.orange : .secondary)
    }

    private func n(_ v: Double) -> String { v >= 1000 ? String(format: "%.1fk", v / 1000) : String(Int(v.rounded())) }
}
