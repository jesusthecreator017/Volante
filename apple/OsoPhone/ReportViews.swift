// The post-session Summary (docs/strokes/report-format.md, OsoKit SessionReport): insight cards and the intensity timeline on a
// session's page, plus the report file to export. Racquet sessions only (the wrist measures hand speed, not head speed).

import Charts
import OsoKit
import SwiftUI

extension SessionStore {
    /// The report of a racquet session with its insights (history: the last 5 earlier real racquet sessions), off the main actor.
    /// `forWeb`: no weekly-goal insight (it would use today's goal and week for an old session); the local UI keeps it.
    @MainActor
    func report(_ meta: SessionMeta, breakdown: StrokeBreakdown?, forWeb: Bool = false) async -> SessionReport? {
        guard meta.source == .podStream || meta.source == .podUpload else { return nil }
        let earlier = sessions.filter { ($0.source == .podStream || $0.source == .podUpload) && $0.createdAt < meta.createdAt }.prefix(5)
        let urls = [imuURL(meta.id)] + earlier.map { imuURL($0.id) }
        let anchor = meta.anchor, hrWall = meta.heartRate
        let started = ISO8601DateFormatter().string(from: meta.createdAt)
        let strokes = breakdown.map { b in SessionReport.Strokes(groups: b.groups.mapValues(\.count)) }
        let (goalMetric, goalTarget) = GoalProgress.saved
        let goal = GoalProgress.compute(self, metric: goalMetric, target: goalTarget)
        let weekly = forWeb ? nil : (value: goal.value, target: Double(goal.target), unit: goalMetric.unit)
        return await Task.detached(priority: .userInitiated) { () -> SessionReport? in
            func build(_ u: URL, hr: [(t: Double, bpm: Double)] = [], strokes: SessionReport.Strokes? = nil) -> SessionReport? {
                guard let d = try? Data(contentsOf: u), let cap = try? ImuFile.parse([UInt8](d)) else { return nil }
                return SessionReporter.build(parts: [cap.samples], startedAt: started, hr: hr, strokes: strokes)
            }
            let hr = anchor.map { a in hrWall.map { (t: a.device($0.t), bpm: $0.bpm) } } ?? []
            guard var r = build(urls[0], hr: hr, strokes: strokes) else { return nil }
            let history = urls.dropFirst().compactMap { build($0) }
            r.insights = InsightRules.insights(for: r, InsightContext(history: history, weeklyGoal: weekly))
            return r
        }.value
    }

    func reportURL(_ id: String) -> URL { folder(id).appendingPathComponent("report.json") }
}

struct InsightsCard: View {
    let report: SessionReport
    var body: some View {
        Card("Insights", systemImage: "sparkles") {
            ForEach(report.insights) { i in
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: icon(i.severity))
                        .font(.title3).foregroundStyle(color(i.severity))
                        .frame(width: 28)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(i.title).font(.subheadline.weight(.semibold))
                        Text(i.body).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                .accessibilityElement(children: .combine)
                if i.id != report.insights.last?.id { Divider() }
            }
            Text("Speeds are estimates from the racquet's rotation.").font(.caption).foregroundStyle(.tertiary)
        }
    }
    private func icon(_ s: SessionReport.Insight.Severity) -> String {
        switch s { case .praise: "hand.thumbsup.fill"; case .tip: "lightbulb.fill"; case .info: "info.circle.fill" }
    }
    private func color(_ s: SessionReport.Insight.Severity) -> Color {
        switch s { case .praise: Theme.good; case .tip: Theme.hot; case .info: .secondary }
    }
}

/// Swings per minute (bars), the minute's hard-swing speed (line) and heart rate (when present), on one time axis.
struct IntensityCard: View {
    let report: SessionReport
    var body: some View {
        let minutes = report.timeline
        let hasHR = minutes.contains { $0.hrBpm != nil }
        Card("Intensity", systemImage: "waveform.path.ecg") {
            Chart {
                ForEach(minutes, id: \.tMin) { m in
                    BarMark(x: .value("Minute", m.tMin), y: .value("Swings", m.swings))
                        .foregroundStyle(Color.accentColor.opacity(0.35))
                }
            }
            .chartXAxisLabel("minute")
            .chartYAxisLabel("swings / min", position: .topLeading)
            .frame(height: 110)
            .accessibilityLabel("Swings per minute over \(minutes.count) minutes, peak \(minutes.map(\.swings).max() ?? 0)")
            Chart {
                ForEach(minutes.filter { $0.p90Kmh != nil }, id: \.tMin) { m in
                    LineMark(x: .value("Minute", m.tMin), y: .value("km/h", m.p90Kmh!), series: .value("s", "speed"))
                        .foregroundStyle(Theme.hot).interpolationMethod(.monotone)
                    PointMark(x: .value("Minute", m.tMin), y: .value("km/h", m.p90Kmh!)).foregroundStyle(Theme.hot).symbolSize(20)
                }
                if hasHR {
                    ForEach(minutes.filter { $0.hrBpm != nil }, id: \.tMin) { m in
                        LineMark(x: .value("Minute", m.tMin), y: .value("bpm", m.hrBpm!), series: .value("s", "hr"))
                            .foregroundStyle(Theme.bad.opacity(0.7)).lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                    }
                }
            }
            .chartYScale(domain: .automatic(includesZero: false))
            .chartYAxisLabel(hasHR ? "hard swing km/h · bpm (dashed)" : "hard swing km/h", position: .topLeading)
            .frame(height: 130)
            .accessibilityLabel("Hard-swing speed per minute" + (hasHR ? " and heart rate" : ""))
            HStack {
                BigStat(value: report.perMinActive.map { String(format: "%.0f", $0) } ?? "–", label: "swings per active min", style: .title3)
                BigStat(value: clock(report.activeS), label: "in rallies", style: .title3)
                BigStat(value: report.medianShots.map { String(format: "%.0f", $0) } ?? "–", label: "shots per rally", style: .title3)
            }
        }
    }
}
