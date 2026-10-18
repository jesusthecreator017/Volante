// Saved sessions: list, header, charts, swings, heart rate, Pi actions, share imu.bin.

import Charts
import OsoKit
import SwiftUI

struct SessionsView: View {
    @Environment(SessionStore.self) private var store
    @Environment(FitbitLink.self) private var fitbit
    @Environment(Router.self) private var router

    var body: some View {
        NavigationStack(path: Bindable(router).path) {
            Group {
                if store.sessions.isEmpty { empty } else { list }
            }
            .navigationTitle("Sessions")
            .task { await fitbit.backfill(store) }
            .navigationDestination(for: SessionMeta.self) { SessionDetailView(meta: $0) }
        }
        #if DEBUG
        .modifier(DebugInsightsRender())
        #endif
    }

    private var list: some View {
        List {
            ForEach(store.sessions) { m in
                NavigationLink(value: m) { SessionRow(meta: m) }
            }
            .onDelete { idx in for i in idx { store.delete(store.sessions[i].id) } }
        }
        .listStyle(.insetGrouped)
        .themedList()
        .canvasNavigationBar()
    }

    private var empty: some View {
        ContentUnavailableView {
            Label("No sessions yet", systemImage: "figure.badminton")
        } description: {
            Text("Play with the pod on your racquet or start a workout on Apple Watch. Your sessions show up here.")
        } actions: {
            Button { router.tab = .pod } label: { Text("Connect pod").padding(.horizontal, 8) }
                .buttonStyle(.glassProminent)
            Button { try? store.importSample() } label: { Text("Load sample session") }
                .buttonStyle(.glass)
        }
    }
}

private struct SessionRow: View {
    @Environment(SessionStore.self) private var store
    let meta: SessionMeta

    var body: some View {
        let row = store.rowSummaries[meta.id]
        HStack(spacing: 12) {
            Image(systemName: meta.source.icon)
                .font(.title3).foregroundStyle(.tint)
                .frame(width: 42, height: 42)
                .background(Color.accentColor.opacity(0.14), in: Circle())
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
    @State private var report: SessionReport?
    @State private var addingHR = false
    @State private var showAllSwings = false
    #if DEBUG
    @State private var debugSwingOpen = false
    #endif

    var body: some View {
        let a = store.analysis(meta)
        let st = a.flatMap { a in store.strokes[meta.id].flatMap { $0.results.count == a.swings.count ? $0 : nil } }
        Screen {
            if let a {
                SessionHeaderView(meta: meta, analysis: a, bests: store.bests)
                if let report, !report.insights.isEmpty { InsightsCard(report: report) }
                if a.mount != .wrist || !meta.heartRate.isEmpty {
                    SessionHealthCard(meta: meta, stats: store.heartStats(meta), span: store.span(meta)) { addingHR = true }
                }
                if let report, report.timeline.count > 1 { IntensityCard(report: report) }
                speedChart(a, st)
                if let st { StrokeBreakdownCard(breakdown: StrokeBreakdown(swings: a.swings, results: st.results)) }
                swings(a, st)
                details(a)
            } else {
                Card { Text("This file could not be read.") }
            }
            if let message {
                Label(message, systemImage: "info.circle").font(.footnote).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 4)
            }
        }
        .navigationTitle(meta.createdAt.formatted(.dateTime.month(.abbreviated).day().hour().minute()))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let a, !a.swings.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    ShareLink(
                        item: SessionCardImage(analysis: a, date: meta.createdAt, personalBest: !store.bests.kinds(for: meta.id).isEmpty,
                                               breakdown: st.map { StrokeBreakdown(swings: a.swings, results: $0.results) },
                                               insight: report?.insights.first { $0.id != "play_longer" }?.title),
                        preview: SharePreview("Volante session, \(meta.createdAt.formatted(date: .abbreviated, time: .omitted))")
                    ) { Label("Share session image", systemImage: "square.and.arrow.up") }
                }
            }
            ToolbarItem(placement: .topBarTrailing) { actionsMenu(a) }
        }
        .sheet(isPresented: $addingHR) { HeartRateImportSheet(meta: $meta) }
        .onChange(of: meta.heartRate.count) {
            Task {
                let st = store.analysis(meta).flatMap { a in store.strokes[meta.id].flatMap { $0.results.count == a.swings.count ? StrokeBreakdown(swings: a.swings, results: $0.results) : nil } }
                report = await store.report(meta, breakdown: st)
            }
        }
        .task {
            await store.loadStrokes(meta)
            let st = store.analysis(meta).flatMap { a in store.strokes[meta.id].flatMap { $0.results.count == a.swings.count ? StrokeBreakdown(swings: a.swings, results: $0.results) : nil } }
            report = await store.report(meta, breakdown: st)
            if let report { try? Data(report.json().utf8).write(to: store.reportURL(meta.id), options: .atomic) }
            await store.loadAllSummaries()
        }
        #if DEBUG
        .navigationDestination(isPresented: $debugSwingOpen) {
            if let a, !a.swings.isEmpty { SwingDetailView(analysis: a, start: debugSwingStart, strokes: st?.results) }
        }
        .onAppear { if a != nil, debugSwingStart > 0 { debugSwingOpen = true } }
        #endif
    }

    #if DEBUG
    /// `-osoSwing N` opens swing N's detail screen (for screenshots).
    private var debugSwingStart: Int {
        let args = ProcessInfo.processInfo.arguments
        return args.firstIndex(of: "-osoSwing").flatMap { Int(args[safe: $0 + 1] ?? "") } ?? 0
    }
    #endif

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

    private func speedChart(_ a: SessionAnalysis, _ st: SessionStrokes?) -> some View {
        let group = { (s: Swing) in st?.results[safe: s.index - 1]?.group ?? .unclassified }
        let present = StrokeGroup.allCases.filter { g in a.swings.contains { group($0) == g } }
        return Card("Swing speed", systemImage: "speedometer") {
            if a.swings.isEmpty {
                Text("No swings detected.").font(.subheadline).foregroundStyle(.secondary)
            } else {
                Chart(a.swings) { s in
                    PointMark(x: .value("Time", s.tStart - a.t0), y: .value("km/h", s.headSpeed * 3.6))
                        .foregroundStyle(by: .value("Stroke group", group(s).title))
                        .symbolSize(s.impact ? 70 : 36)
                }
                .chartForegroundStyleScale(domain: present.map(\.title), range: present.map { Theme.group($0) })
                .chartLegend(.hidden)
                .chartXScale(domain: 0...duration(a))
                .chartXAxis { AxisMarks { v in AxisGridLine(); AxisValueLabel { if let t = v.as(Double.self) { Text(clock(t)).font(.caption2) } } } }
                .chartYAxis { yAxis() }
                .chartYAxisLabel("km/h", position: .topLeading)
                .frame(height: 190)
                .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
                .accessibilityLabel("Swing speed over time")
                .accessibilityValue("\(a.swings.count) swings, best \(String(format: "%.1f", a.summary.maxSpeed ?? 0)) metres per second")
            }
        }
    }

    /// The first 10 swings, the rest behind "Show all": a long session has hundreds.
    private func swings(_ a: SessionAnalysis, _ st: SessionStrokes?) -> some View {
        let shown = showAllSwings ? a.swings : Array(a.swings.prefix(10))
        return Card("Swings (\(a.swings.count))", systemImage: "figure.badminton") {
            if a.swings.isEmpty {
                Text("No swings detected.").font(.subheadline).foregroundStyle(.secondary)
            }
            LazyVStack(spacing: 0) {
                ForEach(shown) { s in
                    NavigationLink { SwingDetailView(analysis: a, start: s.index, strokes: st?.results) } label: { SwingRow(swing: s, result: st?.results[safe: s.index - 1]) }
                        .buttonStyle(.plain)
                        .accessibilityHint("Opens the swing detail")
                    if s.id != shown.last?.id { Divider() }
                }
            }
            if a.swings.count > 10 {
                Button {
                    withAnimation(.snappy) { showAllSwings.toggle() }
                } label: {
                    Label(showAllSwings ? "Show fewer" : "Show all \(a.swings.count) swings", systemImage: showAllSwings ? "chevron.up" : "chevron.down")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.secondary)
                .padding(.top, 4)
            }
        }
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
                if let src = meta.heartRateSource, !meta.heartRate.isEmpty { LabeledContent("Heart rate", value: src) }
            }
            .font(.subheadline)
        }
    }

    // MARK: heart rate source, Pi, sharing

    private func actionsMenu(_ a: SessionAnalysis?) -> some View {
        Menu {
            Section(meta.uploadedToPi.map { "On your Pi since \($0.formatted(date: .abbreviated, time: .shortened))" } ?? "Raspberry Pi") {
                Button { Task { await uploadToPi() } } label: {
                    Label(meta.uploadedToPi == nil ? "Upload to Pi" : "Upload again", systemImage: "icloud.and.arrow.up")
                }
                .disabled(pi.busy)
                Button { Task { await fetchReplay() } } label: { Label("Get 3D replay", systemImage: "cube") }
            }
            Button { addingHR = true } label: { Label(meta.heartRate.isEmpty ? "Add heart rate…" : "Replace heart rate…", systemImage: "heart.text.square") }
            Section("Files") {
                if store.hasReplay(meta.id) {
                    ShareLink(item: store.replayURL(meta.id)) { Label("Share replay.bin", systemImage: "cube") }
                }
                ShareLink(item: store.imuURL(meta.id)) { Label("Share imu.bin", systemImage: "doc") }
                if report != nil {
                    ShareLink(item: store.reportURL(meta.id)) { Label("Export report (JSON)", systemImage: "doc.text") }
                }
            }
        } label: {
            Label("More", systemImage: "ellipsis")
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
}

struct SwingRow: View {
    let swing: Swing
    var result: StrokeResult?

    var body: some View {
        let s = swing, r = result
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(r.map { Theme.group($0.group) } ?? Theme.group(.unclassified)).frame(width: 12, height: 12).padding(.top, 6).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 8) {
                    Text("#\(s.index)").font(.subheadline.weight(.semibold))
                    if let r { StrokeChip(result: r) }
                }
                if let r { BetaGuessLine(result: r, showInfo: false) }
                Text([
                    String(format: "%.0f dps", s.peakDps),
                    s.timing.map { "impact \($0)" },
                    s.handleTiltDeg.map { String(format: "tilt %.0f°", $0) },
                    s.heartRate.map { String(format: "%.0f bpm", $0) },
                    s.confidence < 0.8 ? "low confidence" : nil,
                ].compactMap { $0 }.joined(separator: " · "))
                .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(String(format: "%.1f", s.headSpeed)).font(.score(.title3))
                Text("km/h").font(.caption2).foregroundStyle(.secondary)
            }
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary).padding(.top, 8)
                .accessibilityHidden(true)
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
