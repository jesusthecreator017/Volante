// Summary tab: a recap of the last session against your usual, then trends for a week, month, year or all time
// (swings, speed, time played, strokes, heart rate) and all-time bests. Built from the store's per-session summaries.

import Charts
import OsoKit
import SwiftUI

struct SummaryView: View {
    @Environment(SessionStore.self) private var store
    @Environment(Router.self) private var router
    @AppStorage("volante.summaryRange") private var range: Period = .week

    enum Period: String, CaseIterable, Identifiable {
        case week = "W", month = "M", year = "Y", all = "All"
        var id: String { rawValue }
        var days: Int? { switch self { case .week: 7; case .month: 30; case .year: 365; case .all: nil } }
        var name: String { switch self { case .week: "this week"; case .month: "this month"; case .year: "this year"; case .all: "all time" } }
        var previousName: String { switch self { case .week: "last week"; case .month: "last month"; case .year: "last year"; case .all: "" } }
        /// Chart buckets: days for a week or month, weeks for a year, months for all time.
        var bucket: Calendar.Component { switch self { case .week, .month: .day; case .year: .weekOfYear; case .all: .month } }
    }

    struct Entry: Identifiable {
        let meta: SessionMeta
        let row: SessionStore.RowSummary
        var id: String { meta.id }
        var date: Date { meta.createdAt }
        var avgBpm: Double? { meta.heartRate.isEmpty ? nil : meta.heartRate.map(\.bpm).reduce(0, +) / Double(meta.heartRate.count) }
    }

    /// Real sessions; the sample only when there is nothing else, so a new player still sees what this looks like.
    private var entries: [Entry] {
        let all = store.sessions.compactMap { m in store.rowSummaries[m.id].flatMap { $0.readable ? Entry(meta: m, row: $0) : nil } }
        let real = all.filter { $0.meta.source != .sample }
        return real.isEmpty ? all : real
    }
    private var showingSample: Bool { !entries.isEmpty && entries.allSatisfy { $0.meta.source == .sample } }

    private func inPeriod(_ e: [Entry], offset: Int = 0) -> [Entry] {
        guard let d = range.days else { return offset == 0 ? e : [] }
        let now = Date()
        let end = now.addingTimeInterval(-Double(d * offset) * 86400), start = end.addingTimeInterval(-Double(d) * 86400)
        return e.filter { $0.date > start && $0.date <= end }
    }

    var body: some View {
        NavigationStack {
            Group {
                if store.sessions.isEmpty { empty } else { content }
            }
            .navigationTitle("Summary")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { router.showSettings = true } label: { Label("Settings", systemImage: "gearshape") }
                }
            }
            .task { await store.loadAllSummaries() }
        }
    }

    private var empty: some View {
        ContentUnavailableView {
            Label("Your game, summed up", systemImage: "chart.bar.xaxis")
        } description: {
            Text("After your first session you'll see a recap here, plus how your swings, speed and time on court change week to week.")
        } actions: {
            Button { router.tab = .pod } label: { Text("Start playing").padding(.horizontal, 8) }.buttonStyle(.glassProminent)
            Button { router.openSample(store) } label: { Text("Try the sample session") }.buttonStyle(.glass)
        }
    }

    private var content: some View {
        let all = entries, now = inPeriod(all), before = inPeriod(all, offset: 1)
        return Screen {
            if showingSample {
                Label("Showing the sample session. Your own sessions replace it.", systemImage: "testtube.2")
                    .font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
            }
            GoalCard()
            if let last = all.first { recap(last, others: Array(all.dropFirst().prefix(10))) }
            Picker("Period", selection: $range) {
                ForEach(Period.allCases) { Text($0 == .all ? "All" : $0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding(.top, 4)
            totals(now, before)
            if now.isEmpty {
                Card { Text("No sessions \(range.name). Pick a longer period or go play.").font(.subheadline).foregroundStyle(.secondary) }
            } else {
                swingsChart(now)
                speedChart(now)
                strokeChart(now)
                if now.contains(where: { $0.avgBpm != nil }) { heartChart(now) }
            }
            bests(all)
        }
        .animation(.snappy, value: range)
    }

    // MARK: last session

    private func recap(_ e: Entry, others: [Entry]) -> some View {
        let avgSwings = others.isEmpty ? nil : Double(others.map(\.row.swings).reduce(0, +)) / Double(others.count)
        let bestSpeeds = others.compactMap(\.row.maxSpeed)
        let avgBest = bestSpeeds.isEmpty ? nil : bestSpeeds.reduce(0, +) / Double(bestSpeeds.count)
        return Button { router.tab = .sessions; router.path = [e.meta] } label: {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Label("Last session", systemImage: e.meta.source.icon).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                    Spacer()
                    Text(e.date, format: .relative(presentation: .named)).font(.subheadline).foregroundStyle(.secondary)
                    Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
                }
                HStack(alignment: .top) {
                    BigStat(value: "\(e.row.swings)", label: "Swings", style: .title)
                    BigStat(value: e.row.maxSpeed.map { String(format: "%.1f", $0) } ?? "–", unit: "m/s", label: "Best speed", style: .title)
                    BigStat(value: clock(e.row.durationS), label: "Time", style: .title)
                }
                if let line = recapLine(e, avgSwings: avgSwings, avgBest: avgBest) {
                    Label(line, systemImage: "sparkles").font(.subheadline).foregroundStyle(.primary)
                }
            }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens the session")
    }

    /// One plain sentence comparing the last session with the ones before it.
    private func recapLine(_ e: Entry, avgSwings: Double?, avgBest: Double?) -> String? {
        if let avgBest, let best = e.row.maxSpeed, avgBest > 0 {
            let d = (best - avgBest) / avgBest
            if d > 0.05 { return String(format: "Your best swing was %.0f%% faster than usual.", d * 100) }
            if d < -0.1 { return String(format: "Top speed was %.0f%% below your usual; rest helps.", -d * 100) }
        }
        if let avgSwings, avgSwings > 0 {
            let d = (Double(e.row.swings) - avgSwings) / avgSwings
            if d > 0.15 { return String(format: "%.0f%% more swings than your average session.", d * 100) }
            if d < -0.15 { return "A shorter session than usual." }
            return "Right on your usual pace."
        }
        if e.row.longestRally > 0 { return "Longest rally: \(e.row.longestRally) shots." }
        return nil
    }

    // MARK: totals

    private func totals(_ now: [Entry], _ before: [Entry]) -> some View {
        let swings = now.map(\.row.swings).reduce(0, +), prev = before.map(\.row.swings).reduce(0, +)
        let time = now.map(\.row.durationS).reduce(0, +)
        let best = now.compactMap(\.row.maxSpeed).max()
        return Card {
            StatGrid(minimum: 140) {
                BigStat(value: "\(now.count)", label: now.count == 1 ? "Session" : "Sessions", style: .title)
                BigStat(value: hours(time), label: "On court", style: .title)
                BigStat(value: "\(swings)", label: "Swings", style: .title)
                BigStat(value: best.map { String(format: "%.1f", $0) } ?? "–", unit: "m/s", label: "Best speed", style: .title)
            }
            if range != .all, !before.isEmpty || !now.isEmpty {
                Text(trend(swings, prev)).font(.footnote).foregroundStyle(.secondary)
            }
        }
    }

    private func trend(_ now: Int, _ prev: Int) -> String {
        if prev == 0 { return now == 0 ? "No swings \(range.previousName) either." : "Nothing \(range.previousName) to compare with." }
        let d = Double(now - prev) / Double(prev)
        if abs(d) < 0.05 { return "About the same as \(range.previousName)." }
        return String(format: "%@ %.0f%% from %@.", d > 0 ? "Up" : "Down", abs(d) * 100, range.previousName)
    }

    private func hours(_ s: Double) -> String {
        s >= 3600 ? String(format: "%.1f h", s / 3600) : "\(Int((s / 60).rounded())) min"
    }

    // MARK: charts

    private func swingsChart(_ e: [Entry]) -> some View {
        Card("Swings", systemImage: "figure.badminton") {
            Chart(e) { x in
                BarMark(x: .value("Date", x.date, unit: range.bucket), y: .value("Swings", x.row.swings))
                    .foregroundStyle(Color.accentColor.gradient)
                    .cornerRadius(4)
            }
            .chartXScale(domain: domain)
            .frame(height: 170)
            .accessibilityLabel("Swings per \(range.bucket == .day ? "day" : range.bucket == .month ? "month" : "week")")
        }
    }

    private func speedChart(_ e: [Entry]) -> some View {
        let best = e.filter { $0.row.maxSpeed != nil }
        let avg = best.isEmpty ? nil : best.compactMap(\.row.maxSpeed).reduce(0, +) / Double(best.count)
        return Card("Best speed", systemImage: "speedometer") {
            if best.isEmpty {
                Text("No swings with a speed yet.").font(.subheadline).foregroundStyle(.secondary)
            } else {
                Chart {
                    ForEach(best) { x in
                        LineMark(x: .value("Date", x.date), y: .value("m/s", x.row.maxSpeed ?? 0))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(Theme.hot)
                        PointMark(x: .value("Date", x.date), y: .value("m/s", x.row.maxSpeed ?? 0))
                            .foregroundStyle(Theme.hot)
                    }
                    if let avg {
                        RuleMark(y: .value("Average", avg))
                            .foregroundStyle(.secondary)
                            .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                            .annotation(position: .top, alignment: .leading) {
                                Text(String(format: "avg %.1f", avg)).font(.caption2).foregroundStyle(.secondary)
                            }
                    }
                }
                .chartXScale(domain: domain)
                .chartYScale(domain: .automatic(includesZero: false))
                .chartYAxisLabel("m/s", position: .topLeading)
                .frame(height: 170)
            }
        }
    }

    /// Stroke groups over the period (racquet sessions only; the models do not run on the watch).
    @ViewBuilder
    private func strokeChart(_ e: [Entry]) -> some View {
        let total = e.reduce(into: StrokeBreakdown()) { acc, x in if let b = x.row.breakdown { acc.merge(b) } }
        if total.total > 0 { StrokeBreakdownCard(breakdown: total) }
    }

    private func heartChart(_ e: [Entry]) -> some View {
        let hr = e.filter { $0.avgBpm != nil }
        return Card("Heart rate", systemImage: "heart.fill") {
            Chart(hr) { x in
                PointMark(x: .value("Date", x.date), y: .value("bpm", x.avgBpm ?? 0))
                    .foregroundStyle(Theme.bad)
                    .symbolSize(60)
            }
            .chartXScale(domain: domain)
            .chartYScale(domain: .automatic(includesZero: false))
            .chartYAxisLabel("avg bpm", position: .topLeading)
            .frame(height: 140)
            Text("Average heart rate per session, from Apple Watch or Fitbit.").font(.footnote).foregroundStyle(.secondary)
        }
    }

    private var domain: ClosedRange<Date> {
        let now = Date()
        if let d = range.days { return now.addingTimeInterval(-Double(d) * 86400)...now }
        let first = entries.last?.date ?? now
        return min(first, now.addingTimeInterval(-30 * 86400))...now
    }

    // MARK: bests

    private func bests(_ e: [Entry]) -> some View {
        let fast = e.filter { $0.row.maxSpeed != nil }.max { ($0.row.maxSpeed ?? 0) < ($1.row.maxSpeed ?? 0) }
        let most = e.max { $0.row.swings < $1.row.swings }
        let rally = e.max { $0.row.longestRally < $1.row.longestRally }
        return Card("Personal bests", systemImage: "trophy.fill") {
            VStack(spacing: 10) {
                if let fast { best("Fastest swing", String(format: "%.1f m/s", fast.row.maxSpeed ?? 0), fast) }
                if let most { best("Most swings", "\(most.row.swings)", most) }
                if let rally, rally.row.longestRally > 0 { best("Longest rally", "\(rally.row.longestRally) shots", rally) }
            }
        }
    }

    private func best(_ title: String, _ value: String, _ e: Entry) -> some View {
        Button { router.tab = .sessions; router.path = [e.meta] } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline)
                    Text(e.date, format: .dateTime.month(.abbreviated).day().year()).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Text(value).font(.score(.headline))
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
