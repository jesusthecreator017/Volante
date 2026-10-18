// The Summary's widgets: what exists, the saved layout (order, size, which are shown) and the Edit Summary sheet.

import OsoKit
import SwiftUI

/// Head speed for display: km/h everywhere (OsoKit works in m/s; the style profile, insights and the Arena use km/h).
func kmh(_ metersPerSecond: Double) -> String { String(format: "%.0f", metersPerSecond * 3.6) }

struct SummaryWidget: Codable, Hashable, Identifiable {
    enum Kind: String, Codable, CaseIterable, Identifiable {
        case lastSession, goal, heartRate, effort, zones, recovery, totals, swings, speed, strokes, style, bests
        var id: String { rawValue }
        var title: String {
            switch self {
            case .lastSession: "Last session"
            case .goal: "Weekly goal"
            case .heartRate: "Heart rate"
            case .effort: "Effort"
            case .zones: "Time in zones"
            case .recovery: "Recovery"
            case .totals: "Totals"
            case .swings: "Swings"
            case .speed: "Best speed"
            case .strokes: "Strokes"
            case .style: "Your style (twin)"
            case .bests: "Personal bests"
            }
        }
        var icon: String {
            switch self {
            case .lastSession: "clock"
            case .goal: "target"
            case .heartRate: "heart.fill"
            case .effort: "flame.fill"
            case .zones: "chart.bar.fill"
            case .recovery: "arrow.down.heart"
            case .totals: "sum"
            case .swings: "figure.badminton"
            case .speed: "speedometer"
            case .strokes: "chart.bar.doc.horizontal"
            case .style: "person.2.fill"
            case .bests: "trophy.fill"
            }
        }
        /// Widgets that follow the W / M / Y / All picker.
        var usesPeriod: Bool { [.heartRate, .effort, .zones, .recovery, .totals, .swings, .speed, .strokes].contains(self) }
    }
    enum Size: String, Codable { case small, large }

    var id = UUID()
    var kind: Kind
    var size: Size = .large

    static let defaults: [SummaryWidget] = [
        .init(kind: .lastSession), .init(kind: .goal),
        .init(kind: .heartRate, size: .small), .init(kind: .effort, size: .small),
        .init(kind: .zones), .init(kind: .swings, size: .small), .init(kind: .speed, size: .small),
        .init(kind: .totals), .init(kind: .strokes), .init(kind: .recovery), .init(kind: .style), .init(kind: .bests),
    ]

    static func decode(_ json: String) -> [SummaryWidget] {
        guard !json.isEmpty, let v = try? JSONDecoder().decode([SummaryWidget].self, from: Data(json.utf8)) else { return defaults }
        return v
    }
    static func encode(_ v: [SummaryWidget]) -> String { String(decoding: (try? JSONEncoder().encode(v)) ?? Data(), as: UTF8.self) }
}

/// A half-width tile: a title row, then one number and a caption.
struct SmallTile<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: icon).font(.caption.weight(.semibold)).foregroundStyle(.secondary).lineLimit(1)
            content
        }
        .frame(maxWidth: .infinity, minHeight: 96, alignment: .topLeading)
        .padding(14)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// The weekly goal as a tile: a small ring and the count.
struct GoalTileContent: View {
    @Environment(SessionStore.self) private var store
    @AppStorage(GoalKeys.metric) private var metricRaw = GoalMetric.sessions.rawValue
    @AppStorage(GoalKeys.target) private var target = GoalMetric.sessions.defaultTarget
    var body: some View {
        let metric = GoalMetric(rawValue: metricRaw) ?? .sessions
        let p = GoalProgress.compute(store, metric: metric, target: target)
        HStack(spacing: 10) {
            GoalRing(fraction: p.fraction, lineWidth: 7).frame(width: 40, height: 40)
            VStack(alignment: .leading, spacing: 0) {
                Text("\(Int(p.value.rounded())) / \(p.target)").font(.score(.headline))
                Text(metric.unit + (p.streak > 0 ? " · \(p.streak) wk streak" : "")).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

/// Edit Summary: drag to reorder, swipe to remove, pick small or large, add the hidden ones back.
struct EditSummarySheet: View {
    @Binding var layout: [SummaryWidget]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach($layout) { $w in
                        HStack(spacing: 12) {
                            Image(systemName: w.kind.icon).foregroundStyle(.tint).frame(width: 24)
                            Text(w.kind.title)
                            Spacer()
                            Picker("Size", selection: $w.size) {
                                Text("Small").tag(SummaryWidget.Size.small)
                                Text("Large").tag(SummaryWidget.Size.large)
                            }
                            .pickerStyle(.segmented)
                            .frame(width: 130)
                            .labelsHidden()
                        }
                    }
                    .onMove { layout.move(fromOffsets: $0, toOffset: $1) }
                    .onDelete { layout.remove(atOffsets: $0) }
                } header: {
                    Text("Shown")
                } footer: {
                    Text("Drag to reorder, swipe left to remove. Small widgets sit two to a row.")
                }
                let hidden = SummaryWidget.Kind.allCases.filter { k in !layout.contains { $0.kind == k } }
                if !hidden.isEmpty {
                    Section("Add") {
                        ForEach(hidden) { k in
                            Button { withAnimation { layout.append(.init(kind: k)) } } label: {
                                Label { Text(k.title).foregroundStyle(.primary) } icon: { Image(systemName: "plus.circle.fill").foregroundStyle(Theme.good) }
                            }
                        }
                    }
                }
                Section {
                    Button("Reset to default", role: .destructive) { withAnimation { layout = SummaryWidget.defaults } }
                }
            }
            .themedList()
            .environment(\.editMode, .constant(.active))
            .navigationTitle("Edit Summary")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done", systemImage: "checkmark") { dismiss() } } }
        }
    }
}
