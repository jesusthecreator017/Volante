// Summary tab: a customizable dashboard (Edit: reorder, remove, resize, add widgets; saved per phone). Widgets cover the last
// session with its health, the weekly goal, trends for a week / month / year / all time (swings, speed, strokes, heart rate,
// effort, time in zones, recovery) and personal bests. Built from the store's per-session summaries and attached heart rate.

import Charts
import OsoKit
import SwiftUI

struct SummaryView: View {
    @Environment(SessionStore.self) private var store
    @Environment(Router.self) private var router
    @AppStorage("volante.summaryRange") private var range: Period = .week
    @AppStorage("volante.summary.layout") private var layoutJSON = ""
    @State private var editing = false

    private var layout: [SummaryWidget] {
        get { SummaryWidget.decode(layoutJSON) }
        nonmutating set { layoutJSON = SummaryWidget.encode(newValue) }
    }

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
                ToolbarItem(placement: .topBarLeading) {
                    if !store.sessions.isEmpty { Button("Edit") { editing = true } }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { router.showSettings = true } label: { Label("Settings", systemImage: "gearshape") }
                }
            }
            .task { await store.loadAllSummaries() }
            .sheet(isPresented: $editing) { EditSummarySheet(layout: Binding(get: { layout }, set: { layout = $0 })) }
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
        let items = layout
        return Screen {
            if showingSample {
                Label("Showing the sample session. Your own sessions replace it.", systemImage: "testtube.2")
                    .font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
            }
            if items.contains(where: { $0.kind.usesPeriod }) {
                Picker("Period", selection: $range) {
                    ForEach(Period.allCases) { Text($0 == .all ? "All" : $0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
            }
            // small widgets pair up two per row; large ones take the row
            ForEach(rows(items), id: \.first!.id) { row in
                if row.count == 2 || row.first!.size == .small {
                    HStack(alignment: .top, spacing: 12) {
                        ForEach(row) { w in tile(w.kind, all: all, now: now, before: before).frame(maxWidth: .infinity) }
                        if row.count == 1 { Color.clear.frame(maxWidth: .infinity, maxHeight: 1) }
                    }
                } else {
                    card(row.first!.kind, all: all, now: now, before: before)
                }
            }
            if items.isEmpty {
                ContentUnavailableView("No widgets", systemImage: "square.grid.2x2", description: Text("Tap Edit to add some."))
            }
        }
        .animation(.snappy, value: range)
        .animation(.snappy, value: layoutJSON)
    }

    private func rows(_ items: [SummaryWidget]) -> [[SummaryWidget]] {
        var out: [[SummaryWidget]] = [], pending: SummaryWidget?
        for w in items {
            if w.size == .small {
                if let p = pending { out.append([p, w]); pending = nil } else { pending = w }
            } else {
                if let p = pending { out.append([p]); pending = nil }
                out.append([w])
            }
        }
        if let p = pending { out.append([p]) }
        return out
    }

    @ViewBuilder
    private func card(_ k: SummaryWidget.Kind, all: [Entry], now: [Entry], before: [Entry]) -> some View {
        switch k {
        case .goal: GoalCard()
        case .style: TwinCard()
        case .lastSession: if let last = all.first { recap(last, others: Array(all.dropFirst().prefix(10))) }
        case .totals: totals(now, before)
        case .swings: if !now.isEmpty { swingsChart(now) } else { emptyPeriod("Swings") }
        case .speed: if !now.isEmpty { speedChart(now) } else { emptyPeriod("Best speed") }
        case .strokes: strokeChart(now)
        case .heartRate: heartChart(now)
        case .effort: effortChart(now)
        case .zones: zonesCard(now)
        case .recovery: recoveryChart(now)
        case .bests: bests(all)
        }
    }

    /// The small version: one number that matters, tappable where it leads somewhere.
    @ViewBuilder
    private func tile(_ k: SummaryWidget.Kind, all: [Entry], now: [Entry], before: [Entry]) -> some View {
        let hs = now.compactMap { e in store.heartStats(e.meta, exact: false).map { (e, $0) } }
        switch k {
        case .goal: SmallTile(title: "Weekly goal", icon: "target") { GoalTileContent() }
        case .style: SmallTile(title: "Your style", icon: "person.2.fill") { Text("Twin profile").font(.headline) }
        case .lastSession:
            SmallTile(title: "Last session", icon: "clock") {
                if let e = all.first {
                    Text("\(e.row.swings) swings").font(.score(.title3))
                    if let h = store.heartStats(e.meta, exact: false) { Text("\(Int(h.avg)) bpm avg · \(h.effortLabel)").font(.caption).foregroundStyle(.secondary) }
                    else { Text(clock(e.row.durationS)).font(.caption).foregroundStyle(.secondary) }
                }
            }
        case .totals: SmallTile(title: range.name.capitalized, icon: "sum") {
            Text("\(now.count) sessions").font(.score(.title3))
            Text(hours(now.map(\.row.durationS).reduce(0, +)) + " on court").font(.caption).foregroundStyle(.secondary)
        }
        case .swings: SmallTile(title: "Swings", icon: "figure.badminton") {
            Text("\(now.map(\.row.swings).reduce(0, +))").font(.score(.title3))
            Text(range.name).font(.caption).foregroundStyle(.secondary)
        }
        case .speed: SmallTile(title: "Best speed", icon: "speedometer") {
            Text(now.compactMap(\.row.maxSpeed).max().map { kmh($0) } ?? "–").font(.score(.title3))
            Text("km/h, estimated").font(.caption).foregroundStyle(.secondary)
        }
        case .strokes: SmallTile(title: "Strokes", icon: "chart.bar.doc.horizontal") {
            let b = now.reduce(into: StrokeBreakdown()) { acc, x in if let v = x.row.breakdown { acc.merge(v) } }
            Text("\(b.total)").font(.score(.title3))
            Text("classified (experimental)").font(.caption).foregroundStyle(.secondary)
        }
        case .heartRate: SmallTile(title: "Heart rate", icon: "heart.fill") {
            if hs.isEmpty { Text("–").font(.score(.title3)); Text("no heart rate yet").font(.caption).foregroundStyle(.secondary) }
            else { Text("\(Int(hs.map(\.1.avg).reduce(0, +) / Double(hs.count))) bpm").font(.score(.title3)); Text("average while playing").font(.caption).foregroundStyle(.secondary) }
        }
        case .effort: SmallTile(title: "Effort", icon: "flame.fill") {
            Text("\(Int(hs.map(\.1.effort).reduce(0, +)))").font(.score(.title3))
            Text("points \(range.name)").font(.caption).foregroundStyle(.secondary)
        }
        case .zones: SmallTile(title: "Time in zones", icon: "chart.bar.fill") {
            let z = zoneTotals(hs.map(\.1))
            ZoneBars(stats: z, compact: true).padding(.vertical, 4)
            Text(clock(z.zoneSeconds[3] + z.zoneSeconds[4]) + " in zones 4–5").font(.caption).foregroundStyle(.secondary)
        }
        case .recovery: SmallTile(title: "Recovery", icon: "arrow.down.heart") {
            let r = hs.compactMap(\.1.recovery)
            Text(r.isEmpty ? "–" : "\(Int(r.reduce(0, +) / Double(r.count))) bpm").font(.score(.title3))
            Text("drop 1 min after play").font(.caption).foregroundStyle(.secondary)
        }
        case .bests: SmallTile(title: "Fastest swing", icon: "trophy.fill") {
            Text(all.compactMap(\.row.maxSpeed).max().map { kmh($0) } ?? "–").font(.score(.title3))
            Text("km/h, all time").font(.caption).foregroundStyle(.secondary)
        }
        }
    }

    private func emptyPeriod(_ title: String) -> some View {
        Card(title) { Text("No sessions \(range.name). Pick a longer period or go play.").font(.subheadline).foregroundStyle(.secondary) }
    }

    // MARK: health over the period

    private func healthEntries(_ e: [Entry]) -> [(Entry, HeartStats)] {
        e.compactMap { x in store.heartStats(x.meta, exact: false).map { (x, $0) } }
    }

    private func zoneTotals(_ hs: [HeartStats]) -> HeartStats {
        var z = [Double](repeating: 0, count: 5)
        for h in hs { for i in 0..<5 { z[i] += h.zoneSeconds[i] } }
        return HeartStats(avg: 0, max: 0, min: 0, zoneSeconds: z, effort: 0, recovery: nil, samples: 0, maxHR: store.maxHR)
    }

    private func noHealth(_ title: String, _ icon: String) -> some View {
        Card(title, systemImage: icon) {
            Text("No heart rate \(range.name). Wear your Apple Watch or Fitbit while you play, or open a session and use Add heart rate.")
                .font(.subheadline).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func effortChart(_ e: [Entry]) -> some View {
        let hs = healthEntries(e)
        if hs.isEmpty { noHealth("Effort", "flame.fill") } else {
            Card("Effort", systemImage: "flame.fill") {
                Chart(hs, id: \.0.id) { x, h in
                    BarMark(x: .value("Date", x.date, unit: range.bucket), y: .value("Effort", h.effort))
                        .foregroundStyle(Theme.hot.gradient).cornerRadius(4)
                }
                .chartXScale(domain: domain)
                .frame(height: 150)
                .accessibilityLabel("Effort per \(range.bucket == .day ? "day" : "period"), total \(Int(hs.map(\.1.effort).reduce(0, +))) points")
                Text("Effort is minutes in each heart-rate zone weighted by the zone (1–5). Under 50 is light, 50–100 moderate, 100–200 hard.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func zonesCard(_ e: [Entry]) -> some View {
        let hs = healthEntries(e).map(\.1)
        if hs.isEmpty { noHealth("Time in zones", "chart.bar.fill") } else {
            Card("Time in zones", systemImage: "chart.bar.fill") {
                ZoneBars(stats: zoneTotals(hs))
                Text("Zones are shares of your max heart rate (\(Int(store.maxHR)) bpm; change it in Settings).").font(.footnote).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func recoveryChart(_ e: [Entry]) -> some View {
        let hs = healthEntries(e).filter { $0.1.recovery != nil }
        if hs.isEmpty { noHealth("Recovery", "arrow.down.heart") } else {
            Card("Recovery", systemImage: "arrow.down.heart") {
                Chart(hs, id: \.0.id) { x, h in
                    LineMark(x: .value("Date", x.date), y: .value("bpm", h.recovery!)).foregroundStyle(Theme.good)
                    PointMark(x: .value("Date", x.date), y: .value("bpm", h.recovery!)).foregroundStyle(Theme.good)
                }
                .chartXScale(domain: domain)
                .chartYAxisLabel("bpm drop in 1 min", position: .topLeading)
                .frame(height: 140)
                Text("How far your heart rate falls in the minute after play. It tends to grow as fitness improves.").font(.footnote).foregroundStyle(.secondary)
            }
        }
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
                    BigStat(value: e.row.maxSpeed.map { kmh($0) } ?? "–", unit: "km/h", label: "Best speed", style: .title)
                    BigStat(value: clock(e.row.durationS), label: "Time", style: .title)
                }
                if let h = store.heartStats(e.meta, exact: false) {
                    HStack(spacing: 14) {
                        Label("\(Int(h.avg)) avg · \(Int(h.max)) max bpm", systemImage: "heart.fill").foregroundStyle(Theme.bad)
                        Label(h.effortLabel, systemImage: "flame.fill").foregroundStyle(Theme.hot)
                        if let r = h.recovery { Label("−\(Int(r))", systemImage: "arrow.down.heart").foregroundStyle(Theme.good) }
                    }
                    .font(.subheadline.weight(.semibold))
                    ZoneBars(stats: h, compact: true)
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
                BigStat(value: best.map { kmh($0) } ?? "–", unit: "km/h", label: "Best speed", style: .title)
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
                        LineMark(x: .value("Date", x.date), y: .value("km/h", (x.row.maxSpeed ?? 0) * 3.6))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(Theme.hot)
                        PointMark(x: .value("Date", x.date), y: .value("km/h", (x.row.maxSpeed ?? 0) * 3.6))
                            .foregroundStyle(Theme.hot)
                    }
                    if let avg {
                        RuleMark(y: .value("Average", avg * 3.6))
                            .foregroundStyle(.secondary)
                            .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                            .annotation(position: .top, alignment: .leading) {
                                Text("avg " + kmh(avg)).font(.caption2).foregroundStyle(.secondary)
                            }
                    }
                }
                .chartXScale(domain: domain)
                .chartYScale(domain: .automatic(includesZero: false))
                .chartYAxisLabel("km/h", position: .topLeading)
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

    @ViewBuilder
    private func heartChart(_ e: [Entry]) -> some View {
        let hs = healthEntries(e)
        if hs.isEmpty { noHealth("Heart rate", "heart.fill") } else {
            Card("Heart rate", systemImage: "heart.fill") {
                Chart {
                    ForEach(hs, id: \.0.id) { x, h in
                        RuleMark(x: .value("Date", x.date), yStart: .value("avg", h.avg), yEnd: .value("max", h.max))
                            .foregroundStyle(Theme.bad.opacity(0.35)).lineStyle(StrokeStyle(lineWidth: 6, lineCap: .round))
                        PointMark(x: .value("Date", x.date), y: .value("bpm", h.avg)).foregroundStyle(Theme.bad).symbolSize(50)
                    }
                }
                .chartXScale(domain: domain)
                .chartYScale(domain: .automatic(includesZero: false))
                .chartYAxisLabel("bpm", position: .topLeading)
                .frame(height: 150)
                .accessibilityLabel("Average and max heart rate per session")
                Text("Dot: average while playing. Bar: up to the session's max. From Apple Watch, Fitbit, Apple Health or an imported file.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
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
                if let fast { best("Fastest swing", kmh(fast.row.maxSpeed ?? 0) + " km/h", fast) }
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
