// The shareable session image: a 1080x1350 court-green card, drawn with plain shapes (no Charts) so ImageRenderer
// reproduces it exactly, with fixed sizes so Dynamic Type never changes the picture.

import CoreTransferable
import OsoKit
import SwiftUI
import UniformTypeIdentifiers

struct ShareCardView: View {
    static let size = CGSize(width: 1080, height: 1350)

    let analysis: SessionAnalysis
    let date: Date
    /// Whether this session holds a personal best.
    var personalBest = false

    private var s: SessionSummary { analysis.summary }
    private let ink = Color.white
    private let soft = Color.white.opacity(0.92)

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text("oso").font(.system(size: 120, weight: .black, design: .rounded)).foregroundStyle(ink)
                Spacer()
                if personalBest {
                    Label("Personal best", systemImage: "trophy.fill")
                        .font(.system(size: 40, weight: .heavy, design: .rounded)).foregroundStyle(Color(red: 0.06, green: 0.29, blue: 0.2))
                        .padding(.horizontal, 28).padding(.vertical, 14)
                        .background(Color(red: 1, green: 0.78, blue: 0.24), in: Capsule())
                }
            }
            Text(date.formatted(.dateTime.weekday(.wide).month(.wide).day().year()))
                .font(.system(size: 46, weight: .semibold, design: .rounded)).foregroundStyle(soft)
                .padding(.top, 4)

            Spacer(minLength: 24)

            VStack(alignment: .leading, spacing: 0) {
                Text(analysis.mount == .wrist ? "Best hand speed" : "Best head speed")
                    .font(.system(size: 44, weight: .semibold, design: .rounded)).foregroundStyle(soft)
                HStack(alignment: .firstTextBaseline, spacing: 16) {
                    Text(s.maxSpeed.map { String(format: "%.1f", $0) } ?? "–")
                        .font(.system(size: 260, weight: .black, design: .rounded).monospacedDigit()).foregroundStyle(ink)
                        .minimumScaleFactor(0.5).lineLimit(1)
                    Text("m/s").font(.system(size: 64, weight: .bold, design: .rounded)).foregroundStyle(soft)
                }
            }

            HStack(spacing: 24) {
                stat(clock(s.durationS), "Duration")
                stat("\(s.swingCount)", "Swings")
                stat("\(s.hitCount)", "Hits")
            }
            .padding(.top, 20)

            Spacer(minLength: 24)

            mix.padding(.bottom, 36)
            Sparkline(values: analysis.swings.map(\.headSpeed))
                .frame(height: 230)
                .accessibilityHidden(true)
            Text("Swing speed through the session").font(.system(size: 30, weight: .medium, design: .rounded)).foregroundStyle(soft).padding(.top, 10)
        }
        .padding(72)
        .frame(width: Self.size.width, height: Self.size.height)
        .background(
            ZStack {
                LinearGradient(colors: [Color(red: 0.118, green: 0.478, blue: 0.329), Color(red: 0.059, green: 0.29, blue: 0.2)], startPoint: .topLeading, endPoint: .bottomTrailing)
                Court().stroke(.white.opacity(0.07), lineWidth: 6).padding(36)
            }
        )
        .environment(\.colorScheme, .dark) // stroke colours: the brighter set, readable on green
        .environment(\.dynamicTypeSize, .large)
    }

    private func stat(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.system(size: 88, weight: .heavy, design: .rounded).monospacedDigit()).foregroundStyle(ink).lineLimit(1).minimumScaleFactor(0.6)
            Text(label).font(.system(size: 34, weight: .semibold, design: .rounded)).foregroundStyle(soft)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var mix: some View {
        let counts = StrokeType.allCases.compactMap { k in s.strokeCounts[k].flatMap { $0 > 0 ? (k, $0) : nil } }
        let total = max(1, counts.reduce(0) { $0 + $1.1 })
        return VStack(alignment: .leading, spacing: 18) {
            Text("Stroke mix").font(.system(size: 34, weight: .semibold, design: .rounded)).foregroundStyle(soft)
            GeometryReader { geo in
                HStack(spacing: 6) {
                    ForEach(counts, id: \.0) { k, n in
                        RoundedRectangle(cornerRadius: 14).fill(Theme.stroke(k))
                            .frame(width: max(14, (geo.size.width - 6 * CGFloat(counts.count - 1)) * CGFloat(n) / CGFloat(total)))
                    }
                }
            }
            .frame(height: 56)
            HStack(spacing: 28) {
                ForEach(counts.prefix(4), id: \.0) { k, n in
                    HStack(spacing: 10) {
                        Circle().fill(Theme.stroke(k)).frame(width: 22, height: 22)
                        Text("\(k.rawValue.capitalized) \(n)").font(.system(size: 32, weight: .semibold, design: .rounded)).foregroundStyle(ink).lineLimit(1).minimumScaleFactor(0.6)
                    }
                }
            }
        }
    }
}

private struct Sparkline: View {
    let values: [Double]
    var body: some View {
        GeometryReader { geo in
            let hi = max(values.max() ?? 1, 1), n = values.count
            let pts = values.enumerated().map { i, v in
                CGPoint(x: n > 1 ? geo.size.width * CGFloat(i) / CGFloat(n - 1) : geo.size.width / 2, y: geo.size.height * (1 - CGFloat(v / hi) * 0.92) - 4)
            }
            ZStack {
                Path { p in
                    guard let f = pts.first, let l = pts.last else { return }
                    p.move(to: CGPoint(x: f.x, y: geo.size.height)); p.addLine(to: f)
                    pts.dropFirst().forEach { p.addLine(to: $0) }
                    p.addLine(to: CGPoint(x: l.x, y: geo.size.height)); p.closeSubpath()
                }
                .fill(.white.opacity(0.14))
                Path { p in
                    guard let f = pts.first else { return }
                    p.move(to: f)
                    pts.dropFirst().forEach { p.addLine(to: $0) }
                }
                .stroke(.white, style: StrokeStyle(lineWidth: 5, lineCap: .round, lineJoin: .round))
                ForEach(pts.indices, id: \.self) { i in
                    Circle().fill(.white).frame(width: 12, height: 12).position(pts[i])
                }
            }
        }
    }
}

/// Faint badminton court lines behind the card.
private struct Court: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.addRect(r)
        p.move(to: CGPoint(x: r.midX, y: r.minY)); p.addLine(to: CGPoint(x: r.midX, y: r.maxY))
        p.move(to: CGPoint(x: r.minX, y: r.midY)); p.addLine(to: CGPoint(x: r.maxX, y: r.midY))
        return p
    }
}

// MARK: sharing

@MainActor
func renderShareCard(_ analysis: SessionAnalysis, date: Date, personalBest: Bool) -> Data? {
    let r = ImageRenderer(content: ShareCardView(analysis: analysis, date: date, personalBest: personalBest))
    r.scale = 1 // 1 pt = 1 px: exactly 1080x1350
    r.isOpaque = true
    return r.uiImage?.pngData()
}

/// What `ShareLink` hands to the share sheet: a PNG drawn only when the user picks a destination.
struct SessionCardImage: Transferable, Sendable {
    let analysis: SessionAnalysis
    let date: Date
    let personalBest: Bool

    static var transferRepresentation: some TransferRepresentation {
        DataRepresentation(exportedContentType: .png) { card in
            guard let data = await renderShareCard(card.analysis, date: card.date, personalBest: card.personalBest) else {
                throw CocoaError(.fileWriteUnknown)
            }
            return data
        }
        .suggestedFileName("oso-session.png")
    }
}
