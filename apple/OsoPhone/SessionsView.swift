// Saved sessions: list, summary, swings, heart rate, share imu.bin.

import Charts
import OsoKit
import SwiftUI

struct SessionsView: View {
    @Environment(SessionStore.self) private var store

    var body: some View {
        NavigationStack {
            List {
                ForEach(store.sessions) { m in
                    NavigationLink(value: m) {
                        VStack(alignment: .leading) {
                            Text(m.name)
                            Text("\(m.source.rawValue)\(m.note.map { " · \($0)" } ?? "")").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .onDelete { idx in for i in idx { store.delete(store.sessions[i].id) } }
                if store.sessions.isEmpty {
                    Button("Load sample session") { try? store.importSample() }
                }
            }
            .navigationTitle("Sessions")
            .navigationDestination(for: SessionMeta.self) { SessionDetailView(meta: $0) }
        }
    }
}

struct SessionDetailView: View {
    @Environment(SessionStore.self) private var store
    @Environment(FitbitLink.self) private var fitbit
    @Environment(PiServer.self) private var pi
    @State var meta: SessionMeta
    @State private var message: String?

    var body: some View {
        let a = store.analysis(meta)
        List {
            if let a {
                summary(a)
                if !meta.heartRate.isEmpty, let anchor = meta.anchor { heartRateChart(a, anchor) }
                Section("Swings") {
                    ForEach(a.swings) { s in swingRow(s) }
                }
            } else {
                Text("This file could not be read.")
            }
            Section("Heart rate") {
                if meta.heartRate.isEmpty {
                    Text(meta.anchor == nil ? "No clock anchor for this session (connect before recording)." : "No heart rate attached.")
                        .font(.footnote)
                } else {
                    LabeledContent("Source", value: meta.heartRateSource ?? "—")
                }
                if fitbit.connected == true, meta.anchor != nil, let a {
                    Button("Fetch from Fitbit") { Task { await fetchFitbit(a) } }.disabled(fitbit.busy)
                }
                if let message { Text(message).font(.footnote) }
            }
            Section {
                Button(meta.uploadedToPi == nil ? "Upload to Pi" : "Upload to Pi again") { Task { await uploadToPi() } }
                    .disabled(pi.busy)
                Button("Fetch replay from Pi") { Task { await fetchReplay() } }
                if store.hasReplay(meta.id) {
                    ShareLink(item: store.replayURL(meta.id)) { Label("Share replay.bin", systemImage: "cube") }
                }
                ShareLink(item: store.imuURL(meta.id)) { Label("Share imu.bin", systemImage: "square.and.arrow.up") }
            } header: {
                Text("Raspberry Pi")
            } footer: {
                Text(meta.uploadedToPi.map { "Uploaded \($0.formatted(date: .abbreviated, time: .shortened)). " } ?? ""
                     + "The video pipeline on the Pi turns imu.bin + the two camera videos into replay.bin; open it in the web app's Replay page.")
            }
        }
        .navigationTitle(meta.name)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func summary(_ a: SessionAnalysis) -> some View {
        let s = a.summary
        return Section("Summary") {
            LabeledContent("Sensor", value: a.mount == .wrist ? "Apple Watch (wrist)" : "Racquet pod")
            LabeledContent("Duration", value: String(format: "%.0f s (%.0f s active)", s.durationS, s.activeS))
            LabeledContent("Swings / hits", value: "\(s.swingCount) / \(s.hitCount)")
            LabeledContent("Hits per minute", value: String(format: "%.1f", s.hitsPerMin))
            if let p = s.peakDps { LabeledContent("Peak |ω|", value: String(format: "%.0f dps%@", p, s.saturatedGyro > 0 ? " (saturated)" : "")) }
            if let v = s.maxSpeed { LabeledContent(a.mount == .wrist ? "Best hand speed (proxy)" : "Best head speed", value: String(format: "%.1f m/s", v)) }
            LabeledContent("Rallies", value: "\(s.rallyCount) (longest \(s.longestRally) hits)")
            if s.lossPct > 0 { LabeledContent("Packet loss", value: String(format: "%.2f %%", s.lossPct)) }
            if let kcal = meta.activeEnergyKcal { LabeledContent("Active energy", value: String(format: "%.0f kcal", kcal)) }
        }
    }

    private func swingRow(_ s: Swing) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text("#\(s.index) \(s.stroke.rawValue)").bold()
                Spacer()
                Text(String(format: "%.1f m/s", s.headSpeed))
            }
            Text([
                String(format: "%.0f dps", s.peakDps),
                s.timing.map { "impact \($0)" },
                s.side,
                s.handleTiltDeg.map { String(format: "tilt %.0f°", $0) },
                s.heartRate.map { String(format: "%.0f bpm", $0) },
                s.confidence < 0.8 ? "low confidence" : nil,
            ].compactMap { $0 }.joined(separator: " · "))
            .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func heartRateChart(_ a: SessionAnalysis, _ anchor: ClockAnchor) -> some View {
        Section("Heart rate during the session") {
            Chart {
                ForEach(meta.heartRate.filter { $0.t >= anchor.wall(a.t0) - 5 && $0.t <= anchor.wall(a.t1) + 5 }, id: \.t) { h in
                    LineMark(x: .value("s", anchor.device(h.t) - a.t0), y: .value("bpm", h.bpm))
                }
                ForEach(a.swings.filter(\.impact)) { s in
                    RuleMark(x: .value("s", s.tStart - a.t0)).foregroundStyle(.orange.opacity(0.4))
                }
            }
            .chartXAxisLabel("s")
            .frame(height: 160)
        }
    }

    private func uploadToPi() async {
        do {
            try await pi.upload(meta, imu: store.imuURL(meta.id))
            meta.uploadedToPi = Date()
            try store.writeMeta(meta)
            message = "Uploaded to the Pi."
        } catch {
            message = error.localizedDescription
        }
    }

    private func fetchReplay() async {
        do {
            guard let data = try await pi.replay(meta.id) else { message = "No replay on the Pi yet (the pipeline hasn't run)."; return }
            try store.saveReplay(meta.id, data)
            message = "Replay downloaded (\(data.count / 1024) KB)."
        } catch {
            message = error.localizedDescription
        }
    }

    private func fetchFitbit(_ a: SessionAnalysis) async {
        guard let anchor = meta.anchor else { return }
        do {
            let hr = try await fitbit.heartRate(
                from: Date(timeIntervalSince1970: anchor.wall(a.t0) - 60), to: Date(timeIntervalSince1970: anchor.wall(a.t1) + 60))
            guard !hr.isEmpty else { message = "Fitbit has no heart rate for this time yet (it syncs from the tracker)."; return }
            meta.heartRate = hr
            meta.heartRateSource = "Fitbit (Google Health API)"
            try store.writeMeta(meta)
            message = "Attached \(hr.count) heart-rate samples."
        } catch {
            message = error.localizedDescription
        }
    }
}
