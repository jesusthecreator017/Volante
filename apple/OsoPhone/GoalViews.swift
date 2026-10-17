// Summary's weekly goal card: an activity ring, the days played this week, the streak; tap to change the goal.

import SwiftUI

struct GoalCard: View {
    @Environment(SessionStore.self) private var store
    @AppStorage(GoalKeys.metric) private var metricRaw = GoalMetric.sessions.rawValue
    @AppStorage(GoalKeys.target) private var target = GoalMetric.sessions.defaultTarget
    @State private var editing = false

    private var metric: GoalMetric { GoalMetric(rawValue: metricRaw) ?? .sessions }

    var body: some View {
        let p = GoalProgress.compute(store, metric: metric, target: target)
        Button { editing = true } label: {
            HStack(spacing: 18) {
                GoalRing(fraction: p.fraction, lineWidth: 14)
                    .frame(width: 96, height: 96)
                    .overlay {
                        VStack(spacing: 0) {
                            Text(number(p.value)).font(.score(.title2))
                            Text("of \(number(Double(p.target)))").font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                VStack(alignment: .leading, spacing: 10) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("This week").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                        Text(p.met ? "Goal reached" : "\(number(max(0, Double(p.target) - p.value))) \(metric.unit) to go")
                            .font(.headline).foregroundStyle(.primary)
                    }
                    WeekDots(days: p.days)
                    Label(p.streak > 0 ? "\(p.streak)-week streak" : "Start a streak", systemImage: "flame.fill")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(p.streak > 0 ? Theme.hot : .secondary)
                        .symbolEffect(.bounce, value: p.streak)
                }
                Spacer(minLength: 0)
            }
            .padding(18)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Change your weekly goal")
        .sensoryFeedback(.success, trigger: p.met) { old, new in !old && new }
        .sheet(isPresented: $editing) { GoalEditor(metricRaw: $metricRaw, target: $target, best: p.best) }
        .onAppear { p.publish() }
        .onChange(of: "\(p.value)|\(p.target)|\(p.metric.rawValue)|\(p.streak)|\(p.days)") { p.publish() }
    }

    private func number(_ v: Double) -> String { v >= 1000 ? String(format: "%.1fk", v / 1000) : String(Int(v.rounded())) }
}

struct GoalRing: View {
    var fraction: Double
    var lineWidth: CGFloat
    var body: some View {
        ZStack {
            Circle().stroke(Color.accentColor.opacity(0.18), lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: min(1, max(0.001, fraction)))
                .stroke(Color.accentColor.gradient, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                .rotationEffect(.degrees(-90))
            if fraction > 1 { // a second lap past the goal, like the Activity rings
                Circle()
                    .trim(from: 0, to: min(1, fraction - 1))
                    .stroke(Theme.hot.gradient, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            }
        }
        .animation(.spring(duration: 0.8), value: fraction)
        .accessibilityHidden(true)
    }
}

struct WeekDots: View {
    let days: [Bool]
    var body: some View {
        let symbols = Calendar.current.veryShortStandaloneWeekdaySymbols
        let first = Calendar.current.firstWeekday - 1
        HStack(spacing: 6) {
            ForEach(0..<7, id: \.self) { i in
                VStack(spacing: 3) {
                    Circle().fill(days[safe: i] == true ? Color.accentColor : Color.secondary.opacity(0.2)).frame(width: 10, height: 10)
                    Text(symbols[(first + i) % 7]).font(.system(size: 9, weight: .medium)).foregroundStyle(.secondary)
                }
            }
        }
        .accessibilityLabel("Played \(days.filter { $0 }.count) days this week")
    }
}

private struct GoalEditor: View {
    @Binding var metricRaw: String
    @Binding var target: Int
    let best: Int
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let metric = GoalMetric(rawValue: metricRaw) ?? .sessions
        NavigationStack {
            Form {
                Section {
                    Picker("Track", selection: $metricRaw) {
                        ForEach(GoalMetric.allCases) { Text($0.title).tag($0.rawValue) }
                    }
                    .onChange(of: metricRaw) { _, new in target = (GoalMetric(rawValue: new) ?? .sessions).defaultTarget }
                    Stepper(value: $target, in: metric.range, step: metric.step) {
                        LabeledContent("Each week", value: "\(target) \(metric.unit)")
                    }
                } footer: {
                    Text("Meet your goal week after week to build a streak. Your best so far: \(best) \(best == 1 ? "week" : "weeks"). Changing the goal recounts past weeks.")
                }
            }
            .themedList()
            .navigationTitle("Weekly goal")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done", systemImage: "checkmark") { dismiss() } } }
        }
        .presentationDetents([.medium])
    }
}
