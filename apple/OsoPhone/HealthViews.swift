// Health data, one way for every source: a session's heart rate (Apple Watch, Fitbit, Apple Health, or a file) becomes
// zones, effort and recovery (OsoKit HeartStats) on the session page and the Summary. "Add heart rate" fills in a session
// recorded while the tracker was not synced or the watch was not worn. Descriptive numbers only, no medical advice.

import Charts
import HealthKit
import OsoKit
import SwiftUI
import UniformTypeIdentifiers

enum HealthPrefs {
    static let ageKey = "volante.health.age"
    static let maxHRKey = "volante.health.maxHR"
    static let zoneColors: [Color] = [Color(light: 0x8AA6A3, dark: 0x9FBDB9), Color(light: 0x588157, dark: 0xA3B18A),
                                      Color(light: 0xD79A00, dark: 0xFFC83D), Color(light: 0xE5701C, dark: 0xFF8A4C), Color(light: 0xC62828, dark: 0xFF6B6B)]
}

extension SessionStore {
    /// The session's wall-clock span: exact from the clock anchor and the analysis, else the recording's length ending at createdAt
    /// (a pod session is saved when it ends).
    /// `exact` parses the recording (fine on a session page); the Summary uses the row summary's duration instead.
    @MainActor
    func span(_ meta: SessionMeta, exact: Bool = true) -> (start: Double, end: Double)? {
        if exact, let a = analysis(meta), let anchor = meta.anchor { return (anchor.wall(a.t0), anchor.wall(a.t1)) }
        guard let d = rowSummaries[meta.id]?.durationS ?? (exact ? analysis(meta).map({ $0.t1 - $0.t0 }) : nil), d > 0 else { return nil }
        let end = meta.createdAt.timeIntervalSince1970
        return (end - d, end)
    }

    /// Max heart rate: the setting, else 220 - age, else the highest reading in any session (at least 185).
    @MainActor
    var maxHR: Double {
        let d = UserDefaults.standard
        let peak = sessions.flatMap(\.heartRate).map(\.bpm).max()
        return HeartStats.maxHR(setting: d.double(forKey: HealthPrefs.maxHRKey).nonZero, age: d.integer(forKey: HealthPrefs.ageKey).nonZero, observedPeak: peak)
    }

    @MainActor
    func heartStats(_ meta: SessionMeta, exact: Bool = true) -> HeartStats? {
        guard !meta.heartRate.isEmpty, let s = span(meta, exact: exact) else { return nil }
        return HeartStats.compute(meta.heartRate, start: s.start, end: s.end, maxHR: maxHR)
    }
}

private extension Double { var nonZero: Double? { self == 0 ? nil : self } }
private extension Int { var nonZero: Int? { self == 0 ? nil : self } }

/// Heart rate on a session page: the curve coloured by zone, avg / max, effort, recovery and time in zones.
struct SessionHealthCard: View {
    let meta: SessionMeta
    let stats: HeartStats?
    let span: (start: Double, end: Double)?
    let add: () -> Void

    var body: some View {
        Card("Heart rate", systemImage: "heart.fill") {
            if let h = stats, let span {
                chart(h, span)
                StatGrid(minimum: 120) {
                    BigStat(value: String(format: "%.0f", h.avg), unit: "bpm", label: "Average", style: .title2)
                    BigStat(value: String(format: "%.0f", h.max), unit: "bpm", label: "Max (\(Int((h.max / h.maxHR * 100).rounded())) % of max HR)", style: .title2)
                    BigStat(value: String(format: "%.0f", h.effort), label: "Effort · \(h.effortLabel)", style: .title2)
                    BigStat(value: h.recovery.map { String(format: "%.0f", $0) } ?? "–", unit: h.recovery == nil ? nil : "bpm",
                            label: h.recovery == nil ? "Recovery (needs a minute after play)" : "Drop 1 min after play", style: .title2)
                }
                ZoneBars(stats: h)
                Text("\(meta.heartRateSource ?? "Heart rate") · zones use a max heart rate of \(Int(h.maxHR)) bpm (Settings).")
                    .font(.caption).foregroundStyle(.secondary)
                Button(action: add) { Label("Replace heart rate", systemImage: "arrow.triangle.2.circlepath") }.font(.footnote.weight(.semibold))
            } else {
                Text(meta.heartRate.isEmpty ? "No heart rate for this session yet. Wore a watch or tracker that hadn't synced? Add it now."
                                            : "The heart-rate readings don't overlap this session's time.")
                    .font(.subheadline).foregroundStyle(.secondary)
                Button(action: add) { Label("Add heart rate", systemImage: "heart.text.square") }.buttonStyle(.secondary)
            }
        }
    }

    private func chart(_ h: HeartStats, _ span: (start: Double, end: Double)) -> some View {
        let pts = meta.heartRate.filter { $0.t >= span.start - 60 && $0.t <= span.end + 120 }
        return Chart {
            ForEach(Array(pts.enumerated()), id: \.offset) { _, p in
                LineMark(x: .value("Minute", (p.t - span.start) / 60), y: .value("bpm", p.bpm))
                    .interpolationMethod(.monotone)
                    .foregroundStyle(Theme.bad)
            }
            RuleMark(x: .value("End", (span.end - span.start) / 60))
                .foregroundStyle(.secondary.opacity(0.5)).lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                .annotation(position: .top, alignment: .trailing) { Text("end").font(.caption2).foregroundStyle(.secondary) }
        }
        .chartYScale(domain: .automatic(includesZero: false))
        .chartXAxisLabel("minutes")
        .chartYAxisLabel("bpm", position: .topLeading)
        .frame(height: 150)
        .accessibilityLabel("Heart rate during the session: average \(Int(h.avg)), max \(Int(h.max)) beats per minute")
    }
}

/// Time in the 5 zones as a stacked bar with a legend.
struct ZoneBars: View {
    let stats: HeartStats
    var compact = false
    var body: some View {
        let total = max(1, stats.zoneTotal)
        VStack(alignment: .leading, spacing: 6) {
            GeometryReader { g in
                HStack(spacing: 2) {
                    ForEach(0..<5, id: \.self) { z in
                        let w = g.size.width * stats.zoneSeconds[z] / total
                        if w > 0.5 { HealthPrefs.zoneColors[z].frame(width: max(2, w - 2)) }
                    }
                }
            }
            .frame(height: compact ? 8 : 12)
            .clipShape(Capsule())
            if !compact {
                ForEach((0..<5).reversed(), id: \.self) { z in
                    HStack(spacing: 8) {
                        Circle().fill(HealthPrefs.zoneColors[z]).frame(width: 8, height: 8)
                        Text("Zone \(z + 1) · \(HeartStats.zoneNames[z])").font(.caption)
                        Text("\(Int(HeartStats.zoneFloors[z] * 100))–\(z == 4 ? 100 : Int(HeartStats.zoneFloors[z + 1] * 100)) %").font(.caption2).foregroundStyle(.tertiary)
                        Spacer()
                        Text(clock(stats.zoneSeconds[z])).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }
}

// MARK: adding heart rate after the fact

/// Fitbit, Apple Health or a file, for the session's span (plus a few minutes after, for the recovery).
struct HeartRateImportSheet: View {
    @Environment(SessionStore.self) private var store
    @Environment(FitbitLink.self) private var fitbit
    @Environment(\.dismiss) private var dismiss
    @Binding var meta: SessionMeta
    @State private var busy: String?
    @State private var message: String?
    @State private var picking = false

    var body: some View {
        NavigationStack {
            Form {
                if let s = store.span(meta) {
                    Section {
                        LabeledContent("Session", value: "\(Date(timeIntervalSince1970: s.start).formatted(date: .abbreviated, time: .shortened)) – \(Date(timeIntervalSince1970: s.end).formatted(date: .omitted, time: .shortened))")
                    } footer: {
                        Text("Heart rate from this time (and 3 minutes after, for your recovery) is added to the session. Readings already attached are replaced.")
                    }
                    Section("From") {
                        source("Fitbit", "heart.circle.fill", .pink, fitbit.connected == true ? "Your Fitbit, through the Volante server" : "Connect Fitbit under Devices first",
                               enabled: fitbit.connected == true) { try await fitbit.heartRate(from: Date(timeIntervalSince1970: s.start - 60), to: Date(timeIntervalSince1970: s.end + 180)) }
                        source("Apple Health", "heart.text.square.fill", .red, "Apple Watch and apps that write to Health (Garmin, Whoop, Oura, Polar…)",
                               enabled: HKHealthStore.isHealthDataAvailable()) { try await AppleHealthHR.read(from: s.start - 60, to: s.end + 180) }
                        Button { picking = true } label: {
                            row("A file", "doc.badge.plus", .blue, "CSV or JSON export: time and bpm (Fitbit, Garmin, Polar exports work)")
                        }
                        .disabled(busy != nil)
                    }
                } else {
                    Section { Text("This session's time span is unknown, so heart rate can't be matched to it.") }
                }
                if let message { Section { Text(message).font(.subheadline) } }
            }
            .navigationTitle("Add heart rate")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
            .fileImporter(isPresented: $picking, allowedContentTypes: [.commaSeparatedText, .json, .plainText, .data]) { r in
                guard case .success(let url) = r else { return }
                Task {
                    await run("A file") {
                        let ok = url.startAccessingSecurityScopedResource()
                        defer { if ok { url.stopAccessingSecurityScopedResource() } }
                        let day = store.span(meta).map { Date(timeIntervalSince1970: $0.start) }
                        return try HeartRateFile.parse(try Data(contentsOf: url), day: day)
                    }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func row(_ title: String, _ icon: String, _ tint: Color, _ detail: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon).foregroundStyle(.white).frame(width: 30, height: 30)
                .background(tint.gradient, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(title).foregroundStyle(.primary)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if busy == title { ProgressView() }
        }
    }

    private func source(_ title: String, _ icon: String, _ tint: Color, _ detail: String, enabled: Bool,
                        _ fetch: @escaping () async throws -> [HeartRateSample]) -> some View {
        Button { Task { await run(title, fetch) } } label: { row(title, icon, tint, detail) }
            .disabled(!enabled || busy != nil)
    }

    private func run(_ source: String, _ fetch: () async throws -> [HeartRateSample]) async {
        busy = source
        defer { busy = nil }
        do {
            guard let s = store.span(meta) else { return }
            let hr = try await fetch().filter { $0.t >= s.start - 60 && $0.t <= s.end + 180 }
            guard !hr.isEmpty else {
                message = "\(source) has no heart rate for this session's time" + (source == "Fitbit" ? " yet: open the Fitbit app to sync, then try again." : ".")
                return
            }
            meta.heartRate = hr
            meta.heartRateSource = source == "A file" ? "Imported file" : source
            try store.writeMeta(meta)
            message = "Added \(hr.count) heart-rate readings from \(source)."
            dismiss()
        } catch {
            message = "\(source): \(error.localizedDescription)"
        }
    }
}

/// Heart rate from Apple Health for a time window (asks for read permission the first time).
enum AppleHealthHR {
    static let store = HKHealthStore()

    static func read(from: Double, to: Double) async throws -> [HeartRateSample] {
        let type = HKQuantityType(.heartRate)
        try await store.requestAuthorization(toShare: [], read: [type])
        let pred = HKQuery.predicateForSamples(withStart: Date(timeIntervalSince1970: from), end: Date(timeIntervalSince1970: to))
        let desc = HKSampleQueryDescriptor(predicates: [.quantitySample(type: type, predicate: pred)], sortDescriptors: [SortDescriptor(\.startDate)])
        let unit = HKUnit.count().unitDivided(by: .minute())
        return try await desc.result(for: store).map { HeartRateSample(t: $0.startDate.timeIntervalSince1970, bpm: $0.quantity.doubleValue(for: unit)) }
    }
}
