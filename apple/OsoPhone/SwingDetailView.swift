// One swing: big numbers, the |ω| trace with its phases and impact, racquet facts, previous/next.

import Charts
import OsoKit
import SwiftUI

struct SwingDetailView: View {
    let analysis: SessionAnalysis
    /// Stroke results aligned with `analysis.swings` (nil for wrist sessions or before they are computed).
    let strokes: [StrokeResult]?
    @State private var index: Int

    /// `start` is the swing's `index` (1-based) in `analysis.swings`.
    init(analysis: SessionAnalysis, start: Int, strokes: [StrokeResult]? = nil) {
        self.analysis = analysis
        self.strokes = strokes
        _index = State(initialValue: max(0, min(analysis.swings.count - 1, analysis.swings.firstIndex { $0.index == start } ?? 0)))
    }

    private var swings: [Swing] { analysis.swings }

    var body: some View {
        Screen {
            if swings.indices.contains(index) {
                SwingDetailBody(analysis: analysis, swing: swings[index], position: index, count: swings.count, stroke: strokes?[safe: index], go: go)
                    .id(index)
                    .transition(.opacity)
            }
        }
        .navigationTitle(swings.indices.contains(index) ? "Swing \(swings[index].index)" : "Swing")
        .navigationBarTitleDisplayMode(.inline)
        .simultaneousGesture(
            DragGesture(minimumDistance: 40).onEnded { v in
                // a drag that starts at the leading edge is the system back-swipe
                guard v.startLocation.x > 24, abs(v.translation.width) > 2 * abs(v.translation.height) else { return }
                go(v.translation.width < 0 ? 1 : -1)
            }
        )
        .sensoryFeedback(.selection, trigger: index)
        .animation(.easeInOut(duration: 0.18), value: index)
    }

    private func go(_ delta: Int) {
        let next = index + delta
        if swings.indices.contains(next) { index = next }
    }
}

/// The content of a swing screen without the scroll view, so it can also be rendered to an image.
struct SwingDetailBody: View {
    let analysis: SessionAnalysis
    let swing: Swing
    let position: Int
    let count: Int
    var stroke: StrokeResult?
    var go: (Int) -> Void = { _ in }

    var body: some View {
        VStack(spacing: 16) {
            pager
            numbers
            if let stroke { strokeCard(stroke) }
            trace
            facts
            if swing.confidence < 0.8 { lowConfidence }
        }
    }

    // MARK: pager

    private var pager: some View {
        Banner {
            HStack(spacing: 12) {
                step("chevron.left", "Previous swing", enabled: position > 0, delta: -1)
                VStack(spacing: 2) {
                    Text(stroke.map(StrokeChip.text) ?? "Swing \(swing.index)")
                        .font(.system(.title2, design: .rounded, weight: .heavy)).lineLimit(2).minimumScaleFactor(0.7)
                    Text("Swing \(swing.index) of \(count) · at \(clock(swing.start))")
                        .font(.footnote).foregroundStyle(.white.opacity(0.85))
                }
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                step("chevron.right", "Next swing", enabled: position < count - 1, delta: 1)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityAdjustableAction { dir in go(dir == .increment ? 1 : -1) }
    }

    private func step(_ icon: String, _ label: String, enabled: Bool, delta: Int) -> some View {
        Button { go(delta) } label: {
            Image(systemName: icon).font(.headline.weight(.bold))
                .frame(width: 44, height: 44)
                .background(.white.opacity(enabled ? 0.22 : 0.08), in: Circle())
                .opacity(enabled ? 1 : 0.5)
        }
        .disabled(!enabled)
        .dynamicTypeSize(...DynamicTypeSize.xLarge)
        .accessibilityLabel(label)
    }

    // MARK: big numbers

    private var clipped: Bool { swing.saturatedSamples > 0 }

    private var numbers: some View {
        Card {
            StatGrid(minimum: 110) {
                BigStat(value: (clipped ? "≥" : "") + kmh(swing.headSpeed), unit: "km/h",
                        label: analysis.mount == .wrist ? "Hand speed" : "Head speed", style: .title)
                BigStat(value: (clipped ? "≥" : "") + String(format: "%.0f", swing.headSpeed * 3.6), unit: "km/h", label: "Head speed, km/h", style: .title)
                BigStat(value: (clipped ? "≥" : "") + String(format: "%.0f", swing.peakDps), unit: "dps", label: "Peak |ω|", style: .title)
                BigStat(value: swing.impactG.map { String(format: "%.1f", $0) } ?? "–", unit: swing.impactG == nil ? nil : "g",
                        label: swing.impactG == nil ? "No impact detected" : "Impact", style: .title)
            }
            let t = SwingTrace(analysis, swing)
            if t.maxFromBackswing {
                Text(String(format: "Peak from the backswing; forward-swing peak %.0f dps.", t.fwdPeakDps))
                    .font(.footnote).foregroundStyle(.secondary)
                    // Wrap instead of truncating the number at large text sizes
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: stroke (experimental)

    private func strokeCard(_ r: StrokeResult) -> some View {
        Card("Stroke (experimental)", systemImage: "figure.badminton") {
            HStack(spacing: 8) {
                StrokeChip(result: r)
                Spacer(minLength: 0)
            }
            BetaGuessLine(result: r)
            if r.group == .unclassified {
                Text("Not sure about this one. The model needs a clean, complete swing.").font(.footnote).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: trace

    private var trace: some View {
        let t = SwingTrace(analysis, swing)
        return Card("Rotation speed", systemImage: "waveform.path.ecg") {
            if t.points.isEmpty {
                Text("No samples for this swing.").font(.subheadline).foregroundStyle(.secondary)
            } else {
                TraceChart(trace: t, impactLabel: swing.impactLabel)
                    .frame(height: 230)
                    .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(swing.spoken)
                    .accessibilityValue(String(format: "Forward peak %.0f degrees per second, highest in the swing %.0f%@. Backswing, forward swing and follow-through are shaded.",
                                               t.fwdPeakDps, t.maxDps, clipped ? " or more, the sensor maxed out" : ""))
                legend(t)
                if clipped {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill").accessibilityHidden(true)
                        Text("The sensor maxed out for \(swing.saturatedSamples) samples (red bars). The true peak is higher than shown, so the speed is a lower bound.")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .font(.footnote).foregroundStyle(Theme.bad)
                }
            }
        }
    }

    private func legend(_ t: SwingTrace) -> some View {
        StatGrid(minimum: 120) {
            ForEach(t.bands) { b in
                HStack(spacing: 6) {
                    RoundedRectangle(cornerRadius: 3).fill(TraceChart.color(b.phase).opacity(0.35))
                        .overlay(RoundedRectangle(cornerRadius: 3).strokeBorder(TraceChart.color(b.phase), lineWidth: 1.5))
                        .frame(width: 18, height: 12).accessibilityHidden(true)
                    Text("\(b.phase.rawValue) \(Int((b.to - b.from).rounded())) ms").font(.caption)
                }
                .accessibilityElement(children: .combine)
            }
        }
    }

    // MARK: facts

    private var facts: some View {
        Card("Racquet", systemImage: "figure.badminton") {
            VStack(spacing: 8) {
                if let side = swing.side { LabeledContent("Side", value: side.capitalized) }
                if let o = swing.overhead { LabeledContent("Height", value: o ? "Overhead" : "Below the shoulder") }
                if let tilt = swing.handleTiltDeg { LabeledContent("Handle tilt", value: String(format: "%.0f°", tilt)) }
                if let face = swing.faceAngleDeg { LabeledContent("Face angle", value: String(format: "%.0f°", face)) }
                if let label = swing.impactLabel { LabeledContent("Impact", value: label) }
                LabeledContent("Swing time", value: String(format: "%.0f ms", swing.durationMs))
                if let hr = swing.heartRate { LabeledContent("Heart rate", value: String(format: "%.0f bpm", hr)) }
            }
            .font(.subheadline)
        }
    }

    private var lowConfidence: some View {
        Card("Less certain", systemImage: "exclamationmark.triangle") {
            Text(String(format: "Confidence %.0f%%. Treat these numbers with care.", swing.confidence * 100)).font(.subheadline.weight(.semibold))
            ForEach(Array(swing.plainReasons.enumerated()), id: \.offset) { _, r in
                Label(r, systemImage: "circle.fill").labelStyle(BulletStyle()).font(.subheadline)
            }
        }
    }
}

private struct BulletStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            configuration.icon.font(.system(size: 5)).foregroundStyle(.secondary).accessibilityHidden(true)
            configuration.title.foregroundStyle(.primary)
        }
    }
}

/// |ω| over the swing with phase bands, the impact rule and the peak marker.
struct TraceChart: View {
    let trace: SwingTrace
    let impactLabel: String?

    static func color(_ p: SwingTrace.Band.Phase) -> Color {
        switch p {
        case .backswing: Color(light: 0x6B7580, dark: 0xA3AEB8)
        case .forward, .swing: Theme.hot
        case .follow: Theme.courtInk
        }
    }

    var body: some View {
        Chart {
            ForEach(trace.bands) { b in
                RectangleMark(xStart: .value("From", b.from), xEnd: .value("To", b.to), yStart: .value("Min", 0), yEnd: .value("Max", trace.yMax))
                    .foregroundStyle(Self.color(b.phase).opacity(0.16))
            }
            ForEach(trace.clips) { c in
                RectangleMark(xStart: .value("From", c.from), xEnd: .value("To", max(c.to, c.from + 3)), yStart: .value("Min", 0), yEnd: .value("Max", trace.yMax * 0.06))
                    .foregroundStyle(Theme.bad)
            }
            ForEach(trace.points) { p in
                AreaMark(x: .value("ms", p.ms), y: .value("dps", p.dps)).foregroundStyle(Theme.courtInk.opacity(0.15))
                LineMark(x: .value("ms", p.ms), y: .value("dps", p.dps)).foregroundStyle(Theme.courtInk).lineStyle(StrokeStyle(lineWidth: 2))
            }
            if let x = trace.impactMs {
                RuleMark(x: .value("Impact", x))
                    .foregroundStyle(Theme.action)
                    .lineStyle(StrokeStyle(lineWidth: 2, dash: [5, 3]))
                    .annotation(position: .top, alignment: .center, spacing: 2, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                        Text("impact")
                            .font(.caption2.weight(.bold)).foregroundStyle(.primary)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(Theme.card.opacity(0.9), in: Capsule())
                    }
            }
            if trace.showsMax {
                PointMark(x: .value("Max", trace.maxMs), y: .value("Max dps", trace.maxDps))
                    .symbolSize(40).foregroundStyle(Color.secondary)
                    .annotation(position: .trailing, spacing: 3, overflowResolution: .init(x: .fit(to: .chart), y: .fit(to: .chart))) {
                        Text(String(format: "max %.0f%@", trace.maxDps, trace.maxInBackswing ? " (backswing)" : ""))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
            }
            PointMark(x: .value("Forward peak", trace.fwdPeakMs), y: .value("Forward peak dps", trace.fwdPeakDps))
                .symbolSize(110).foregroundStyle(Theme.hot)
                .annotation(position: .topTrailing, spacing: 2, overflowResolution: .init(x: .fit(to: .chart), y: .fit(to: .chart))) {
                    Text("forward peak" + (impactLabel.map { " · \($0)" } ?? ""))
                        .font(.caption2.weight(.bold)).foregroundStyle(.primary)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(Theme.card.opacity(0.85), in: Capsule())
                }
        }
        .chartXScale(domain: trace.xMin...trace.xMax)
        .chartYScale(domain: 0...trace.yMax)
        .chartLegend(.hidden)
        .chartXAxis {
            AxisMarks { v in
                AxisGridLine()
                AxisValueLabel { if let n = v.as(Double.self) { Text("\(Int(n))").font(.caption2) } }
            }
        }
        .chartXAxisLabel("ms from impact", alignment: .center)
        .chartYAxis {
            AxisMarks(position: .leading) { v in
                AxisGridLine()
                AxisValueLabel { if let n = v.as(Double.self) { Text("\(Int(n))").font(.caption2).frame(width: 36, alignment: .trailing) } }
            }
        }
        .chartYAxisLabel("dps", position: .topLeading)
    }
}
