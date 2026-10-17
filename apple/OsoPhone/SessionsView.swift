// Saved sessions: list, header, charts, swings, heart rate, Pi actions, share imu.bin.

import Charts
import OsoKit
import SwiftUI

struct SessionsView: View {
    @Environment(SessionStore.self) private var store
    @Environment(Router.self) private var router

    var body: some View {
        NavigationStack(path: Bindable(router).path) {
            Group {
                if store.sessions.isEmpty { empty } else { list }
            }
            .navigationTitle("Sessions")
            .navigationDestination(for: SessionMeta.self) { SessionDetailView(meta: $0) }
        }
    }

    private var list: some View {
        List {
            ForEach(store.sessions) { m in
                NavigationLink(value: m) { SessionRow(meta: m) }
                    .listRowBackground(Theme.card)
            }
            .onDelete { idx in for i in idx { store.delete(store.sessions[i].id) } }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.canvas.ignoresSafeArea())
        .canvasNavigationBar()
    }

    private var empty: some View {
        Screen {
            Banner {
                Image(systemName: "chart.xyaxis.line").font(.system(size: 44, weight: .semibold)).accessibilityHidden(true)
                Text("No sessions yet").font(.system(.title, design: .rounded, weight: .heavy))
                Text("A session is one visit to the court: every swing, speed and rally. There are three ways to get one.")
                    .font(.subheadline).foregroundStyle(.white.opacity(0.85))
            }
            Card {
                way("sensor.tag.radiowaves.forward", "Racquet pod", "Connect the pod on the Pod tab and record or stream a session.")
                way("applewatch", "Apple Watch", "Start a badminton workout in oso on the watch; it sends the session here when you end it.")
                way("testtube.2", "Sample session", "Explore the app with a built-in synthetic session.")
                Button { try? store.importSample() } label: { Label("Load sample session", systemImage: "play.circle") }
                    .buttonStyle(.primary)
            }
        }
    }

    private func way(_ icon: String, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon).font(.title3).foregroundStyle(.tint).frame(width: 30).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.headline)
                Text(text).font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct SessionRow: View {
    @Environment(SessionStore.self) private var store
    let meta: SessionMeta

    var body: some View {
        let row = store.rowSummaries[meta.id]
        HStack(spacing: 12) {
            Image(systemName: meta.source.icon)
                .font(.title3).foregroundStyle(Theme.courtInk)
                .frame(width: 42, height: 42)
                .background(Theme.courtInk.opacity(0.12), in: Circle())
                .layoutPriority(1)
                .dynamicTypeSize(...DynamicTypeSize.xLarge)
                .accessibilityLabel(meta.source.label)
            VStack(alignment: .leading, spacing: 3) {
                Text(meta.createdAt.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day().hour().minute())).font(.headline)
                Text(([meta.source.label] + [meta.note].compactMap { $0 }).joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            if let row, row.readable {
                VStack(alignment: .trailing, spacing: 0) {
                    Text("\(row.swings)").font(.score(.title2))
                    Text("swings").font(.caption2).foregroundStyle(.secondary)
                    Text(clock(row.durationS)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
        .task { await store.loadRowSummary(meta) }
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
        Screen {
            if let a {
                header(a)
                speedChart(a)
                strokeMix(a)
                if !meta.heartRate.isEmpty, let anchor = meta.anchor { heartRateChart(a, anchor) }
                swings(a)
                details(a)
            } else {
                Card { Text("This file could not be read.") }
            }
            heartRateCard(a)
            piCard
        }
        .navigationTitle(meta.createdAt.formatted(.dateTime.month(.abbreviated).day().hour().minute()))
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: header

    private func header(_ a: SessionAnalysis) -> some View {
        let s = a.summary
        return Banner {
            HStack {
                Label(meta.source.label, systemImage: meta.source.icon).font(.subheadline.weight(.semibold))
                Spacer(minLength: 8)
                if meta.uploadedToPi != nil { StatusChip(text: "On Pi", systemImage: "checkmark", color: .white) }
            }
            Text(meta.name).font(.system(.title3, design: .rounded, weight: .bold))
            StatGrid(minimum: 130) {
                BigStat(value: clock(s.durationS), label: "Duration", onBanner: true)
                BigStat(value: "\(s.swingCount)", unit: "/ \(s.hitCount) hits", label: "Swings", onBanner: true)
                BigStat(value: s.maxSpeed.map { String(format: "%.1f", $0) } ?? "–", unit: "m/s", label: a.mount == .wrist ? "Best hand speed" : "Best head speed", onBanner: true)
                BigStat(value: "\(s.rallyCount)", unit: "longest \(s.longestRally)", label: "Rallies", onBanner: true)
            }
        }
    }

    // MARK: charts

    private func duration(_ a: SessionAnalysis) -> Double { max(1, a.t1 - a.t0) }

    /// Fixed-width y labels so the swing-speed and heart-rate charts share the same plot width (aligned time axes).
    private func yAxis() -> some AxisContent {
        AxisMarks(position: .leading) { v in
            AxisGridLine()
            AxisValueLabel {
                if let n = v.as(Double.self) { Text("\(Int(n))").font(.caption2).frame(width: 30, alignment: .trailing) }
            }
        }
    }

    private func speedChart(_ a: SessionAnalysis) -> some View {
        let present = StrokeType.allCases.filter { k in a.swings.contains { $0.stroke == k } }
        return Card("Swing speed", systemImage: "speedometer") {
            if a.swings.isEmpty {
                Text("No swings detected.").font(.subheadline).foregroundStyle(.secondary)
            } else {
                Chart(a.swings) { s in
                    PointMark(x: .value("Time", s.tStart - a.t0), y: .value("m/s", s.headSpeed))
                        .foregroundStyle(by: .value("Stroke", s.stroke.rawValue))
                        .symbolSize(s.impact ? 70 : 36)
                }
                .chartForegroundStyleScale(domain: present.map(\.rawValue), range: present.map { Theme.stroke($0) })
                .chartLegend(.hidden)
                .chartXScale(domain: 0...duration(a))
                .chartXAxis { AxisMarks { v in AxisGridLine(); AxisValueLabel { if let t = v.as(Double.self) { Text(clock(t)).font(.caption2) } } } }
                .chartYAxis { yAxis() }
                .chartYAxisLabel("m/s", position: .topLeading)
                .frame(height: 190)
                .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
                .accessibilityLabel("Swing speed over time")
                .accessibilityValue("\(a.swings.count) swings, best \(String(format: "%.1f", a.summary.maxSpeed ?? 0)) metres per second")
            }
        }
    }

    private func strokeMix(_ a: SessionAnalysis) -> some View {
        let counts = StrokeType.allCases.compactMap { k in a.summary.strokeCounts[k].flatMap { $0 > 0 ? (k, $0) : nil } }
        return Card("Stroke mix", systemImage: "chart.pie") {
            if counts.isEmpty {
                Text("No strokes to classify.").font(.subheadline).foregroundStyle(.secondary)
            } else {
                Chart(counts, id: \.0) { k, n in
                    SectorMark(angle: .value("Swings", n), innerRadius: .ratio(0.62), angularInset: 1.5)
                        .cornerRadius(4)
                        .foregroundStyle(Theme.stroke(k))
                }
                .chartLegend(.hidden)
                .frame(height: 160)
                .frame(maxWidth: .infinity)
                StatGrid(minimum: 130) {
                    ForEach(counts, id: \.0) { k, n in
                        HStack(spacing: 8) {
                            Circle().fill(Theme.stroke(k)).frame(width: 12, height: 12).accessibilityHidden(true)
                            Text(k.rawValue.capitalized).font(.subheadline)
                            Spacer(minLength: 4)
                            Text("\(n)").font(.score(.headline))
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
    }

    private func heartRateChart(_ a: SessionAnalysis, _ anchor: ClockAnchor) -> some View {
        let samples = meta.heartRate.filter { $0.t >= anchor.wall(a.t0) && $0.t <= anchor.wall(a.t1) }
        return Card("Heart rate", systemImage: "heart.fill") {
            Chart {
                ForEach(samples, id: \.t) { h in
                    LineMark(x: .value("Time", anchor.device(h.t) - a.t0), y: .value("bpm", h.bpm))
                        .foregroundStyle(Theme.bad)
                        .interpolationMethod(.monotone)
                }
                ForEach(a.swings.filter(\.impact)) { s in
                    RuleMark(x: .value("Time", s.tStart - a.t0)).foregroundStyle(Color.accentColor.opacity(0.35))
                }
            }
            .chartXScale(domain: 0...duration(a))
            .chartYScale(domain: .automatic(includesZero: false))
            .chartXAxis { AxisMarks { v in AxisGridLine(); AxisValueLabel { if let t = v.as(Double.self) { Text(clock(t)).font(.caption2) } } } }
            .chartYAxis { yAxis() }
            .chartYAxisLabel("bpm", position: .topLeading)
            .frame(height: 150)
            .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
            Text("Orange lines mark hits.").font(.footnote).foregroundStyle(.secondary)
        }
    }

    // MARK: swings and details

    private func swings(_ a: SessionAnalysis) -> some View {
        Card("Swings", systemImage: "figure.badminton") {
            if a.swings.isEmpty {
                Text("No swings detected.").font(.subheadline).foregroundStyle(.secondary)
            }
            LazyVStack(spacing: 0) {
                ForEach(a.swings) { s in
                    swingRow(s)
                    if s.id != a.swings.last?.id { Divider() }
                }
            }
        }
    }

    private func swingRow(_ s: Swing) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(Theme.stroke(s.stroke)).frame(width: 12, height: 12).padding(.top, 6).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("#\(s.index) \(s.stroke.rawValue.capitalized)").font(.subheadline.weight(.semibold))
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
            Spacer(minLength: 8)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(String(format: "%.1f", s.headSpeed)).font(.score(.title3))
                Text("m/s").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
    }

    private func details(_ a: SessionAnalysis) -> some View {
        let s = a.summary
        return Card("Details", systemImage: "list.bullet") {
            VStack(spacing: 8) {
                LabeledContent("Sensor", value: a.mount == .wrist ? "Apple Watch (wrist)" : "Racquet pod")
                LabeledContent("Active time", value: String(format: "%.0f s of %.0f s", s.activeS, s.durationS))
                LabeledContent("Hits per minute", value: String(format: "%.1f", s.hitsPerMin))
                if let p = s.peakDps { LabeledContent("Peak |ω|", value: String(format: "%.0f dps%@", p, s.saturatedGyro > 0 ? " (saturated)" : "")) }
                if s.lossPct > 0 { LabeledContent("Packet loss", value: String(format: "%.2f %%", s.lossPct)) }
                if let kcal = meta.activeEnergyKcal { LabeledContent("Active energy", value: String(format: "%.0f kcal", kcal)) }
            }
            .font(.subheadline)
        }
    }

    // MARK: heart rate source, Pi, sharing

    private func heartRateCard(_ a: SessionAnalysis?) -> some View {
        Card("Heart rate source", systemImage: "heart") {
            if meta.heartRate.isEmpty {
                Text(meta.anchor == nil ? "No clock anchor for this session (connect before recording)." : "No heart rate attached.")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                LabeledContent("Source", value: meta.heartRateSource ?? "—").font(.subheadline)
            }
            if fitbit.connected == true, meta.anchor != nil, let a {
                Button { Task { await fetchFitbit(a) } } label: { Label("Fetch from Fitbit", systemImage: "arrow.down.heart") }
                    .buttonStyle(.secondary).disabled(fitbit.busy)
            }
            if let message { Text(message).font(.footnote) }
        }
    }

    private var piCard: some View {
        Card("Raspberry Pi and sharing", systemImage: "externaldrive.connected.to.line.below") {
            Button { Task { await uploadToPi() } } label: {
                Label(meta.uploadedToPi == nil ? "Upload to Pi" : "Upload to Pi again", systemImage: "icloud.and.arrow.up")
            }
            .buttonStyle(.primary).disabled(pi.busy)
            Button { Task { await fetchReplay() } } label: { Label("Fetch replay from Pi", systemImage: "icloud.and.arrow.down") }
                .buttonStyle(.secondary)
            if store.hasReplay(meta.id) {
                ShareLink(item: store.replayURL(meta.id)) { Label("Share replay.bin", systemImage: "cube") }.buttonStyle(.secondary)
            }
            ShareLink(item: store.imuURL(meta.id)) { Label("Share imu.bin", systemImage: "square.and.arrow.up") }.buttonStyle(.secondary)
            Text((meta.uploadedToPi.map { "Uploaded \($0.formatted(date: .abbreviated, time: .shortened)). " } ?? "")
                 + "The video pipeline on the Pi turns imu.bin + the two camera videos into replay.bin; open it in the web app's Replay page.")
                .font(.footnote).foregroundStyle(.secondary)
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
