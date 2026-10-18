// The experimental stroke classifier in the UI: a per-swing tag, the 10-stroke "beta" guess and the session breakdown.
// Models: OsoKit StrokeClassifier (pure Swift trees on one player's drill data, so everything here is labelled experimental).

import OsoKit
import SwiftUI

/// "Power · FH", or "Unclassified" when the group model is unsure.
struct StrokeChip: View {
    let result: StrokeResult

    static func text(_ r: StrokeResult) -> String {
        r.group == .unclassified ? "Unclassified" : r.group.title + (r.side.map { " · " + $0.rawValue } ?? "")
    }

    static func spoken(_ r: StrokeResult) -> String {
        guard r.group != .unclassified else { return "Stroke group unclassified" }
        return "Stroke group \(r.group.title)" + (r.side.map { $0 == .fh ? ", forehand" : ", backhand" } ?? "") + ", experimental"
    }

    var body: some View {
        let color = Theme.group(result.group)
        Text(Self.text(result))
            .font(.caption.weight(.bold))
            .lineLimit(1).minimumScaleFactor(0.8)
            .padding(.horizontal, 9).padding(.vertical, 3)
            .foregroundStyle(color)
            .background(color.opacity(0.15), in: Capsule())
            .accessibilityLabel(Self.spoken(result))
    }
}

/// "Beta guess: Smash (62 %)" with an info button; nothing when the 10-stroke model is unsure.
struct BetaGuessLine: View {
    let result: StrokeResult
    var showInfo = true
    @State private var info = false

    static func text(_ r: StrokeResult) -> String? {
        guard let t = r.strokeTitle else { return nil }
        return "Beta guess: \(t) (\(Int((r.strokeConfidence * 100).rounded())) %)"
    }

    var body: some View {
        if let text = Self.text(result) {
            HStack(spacing: 4) {
                Text(text).font(.caption).foregroundStyle(.secondary)
                if showInfo {
                    Button { info = true } label: { Image(systemName: "info.circle").font(.caption) }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                        .frame(minWidth: 44, minHeight: 28)
                        .accessibilityLabel("About the beta guess")
                }
            }
            .alert("Experimental", isPresented: $info) { Button("OK", role: .cancel) {} } message: { Text(StrokeCopy.experimental) }
            .accessibilityElement(children: .combine)
        }
    }
}

enum StrokeCopy {
    static let experimental = "Experimental: trained on one player's drills; often wrong."
    static let groups = "Stroke groups sort a swing by effort: power (smash, clear), mid (drive, drop, lift, net kill, long serve) or touch (net shot, push, short serve). The model was trained on one player's drill session, so treat it as a rough guide."
}

/// Counts and typical speed per group, the FH/BH split and a collapsed list of the 10-stroke guesses. Racquet sessions only.
struct StrokeBreakdownCard: View {
    let breakdown: StrokeBreakdown
    var title = "Stroke groups (experimental)"
    @State private var info = false
    @State private var betaOpen = false

    var body: some View {
        let rows = breakdown.rows
        Card(title, systemImage: "chart.bar.doc.horizontal") {
            if rows.isEmpty {
                Text("No swings to classify.").font(.subheadline).foregroundStyle(.secondary)
            } else {
                let maxCount = max(1, rows.map(\.stat.count).max() ?? 1)
                VStack(spacing: 10) {
                    ForEach(rows, id: \.group) { g, st in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(alignment: .firstTextBaseline) {
                                Circle().fill(Theme.group(g)).frame(width: 12, height: 12).accessibilityHidden(true)
                                Text(g.title).font(.subheadline.weight(.semibold))
                                Spacer(minLength: 8)
                                if let v = st.meanSpeed, g != .unclassified { Text(String(format: "%.1f m/s avg", v)).font(.caption).foregroundStyle(.secondary) }
                                Text("\(st.count)").font(.score(.headline))
                            }
                            GeometryReader { geo in
                                Capsule().fill(Theme.group(g).opacity(0.8)).frame(width: max(6, geo.size.width * CGFloat(st.count) / CGFloat(maxCount)))
                            }
                            .frame(height: 6).accessibilityHidden(true)
                        }
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel("\(g.title), \(st.count) swings" + (st.meanSpeed.map { String(format: ", average %.1f metres per second", $0) } ?? ""))
                    }
                }
                if breakdown.fh + breakdown.bh > 0 {
                    LabeledContent("Forehand / backhand", value: "\(breakdown.fh) / \(breakdown.bh)").font(.subheadline)
                }
                if !breakdown.topGuesses.isEmpty {
                    DisclosureGroup(isExpanded: $betaOpen) {
                        VStack(spacing: 6) {
                            ForEach(breakdown.topGuesses.prefix(5), id: \.stroke) { g in
                                LabeledContent(StrokeResult.title(g.stroke), value: "\(g.count)").font(.subheadline)
                            }
                        }
                        .padding(.top, 6)
                    } label: {
                        Text("Beta: stroke guesses").font(.subheadline.weight(.semibold))
                    }
                }
                Button { info = true } label: { Label(StrokeCopy.experimental, systemImage: "info.circle").font(.footnote) }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
                    .alert("About stroke groups", isPresented: $info) { Button("OK", role: .cancel) {} } message: {
                        Text(StrokeCopy.groups + "\n\n" + StrokeCopy.experimental)
                    }
            }
        }
    }
}
