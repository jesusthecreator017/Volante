// The banner at the top of a session: totals, the personal-best badge and the "Best: ..." context.

import OsoKit
import SwiftUI

extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}

struct SessionHeaderView: View {
    let meta: SessionMeta
    let analysis: SessionAnalysis
    let bests: PersonalBests

    var body: some View {
        let s = analysis.summary, kinds = bests.kinds(for: meta.id)
        Banner {
            HStack(spacing: 8) {
                Label(meta.source.label, systemImage: meta.source.icon).font(.subheadline.weight(.semibold))
                Spacer(minLength: 8)
                if !kinds.isEmpty {
                    StatusChip(text: "Personal best!", systemImage: "trophy.fill", color: .white)
                        .accessibilityLabel("Personal best: \(kinds.map(\.rawValue).joined(separator: ", "))")
                }
                if meta.uploadedToPi != nil { StatusChip(text: "On Pi", systemImage: "checkmark", color: .white) }
            }
            Text(meta.name).font(.system(.title3, design: .rounded, weight: .bold))
            StatGrid(minimum: 130) {
                BigStat(value: clock(s.durationS), label: "Duration", onBanner: true)
                BigStat(value: "\(s.swingCount)", unit: "/ \(s.hitCount) hits", label: "Swings", onBanner: true)
                BigStat(value: s.maxSpeed.map { String(format: "%.1f", $0) } ?? "–", unit: "m/s", label: analysis.mount == .wrist ? "Best hand speed" : "Best head speed", onBanner: true)
                BigStat(value: "\(s.rallyCount)", unit: "longest \(s.longestRally)", label: "Rallies", onBanner: true)
            }
            if !kinds.isEmpty {
                Text(kinds.map(\.rawValue).joined(separator: ", ").capitalizedFirst)
                    .font(.footnote.weight(.semibold))
            }
            if let best = bests.context(for: analysis.mount), meta.source != .sample {
                Text(best + (kinds.contains(.headSpeed) || kinds.contains(.handSpeed) ? " (this session)" : ""))
                    .font(.footnote).foregroundStyle(.white.opacity(0.85))
            }
        }
    }
}

private extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
