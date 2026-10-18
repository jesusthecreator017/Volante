// Volante Twins on the phone (docs/twins-app.md): the style profile from your pod sessions, the build your twin gets, and
// my_style.json to train it with the kit. Computed here; only the profile (a few numbers) ever leaves the phone: the exported file,
// and volante web when "Show my sessions on volante web" is on (WebSync).

import OsoKit
import SwiftUI

/// The Summary card that opens the Twin screen.
struct TwinCard: View {
    var body: some View {
        NavigationLink { TwinView() } label: {
            HStack(spacing: 14) {
                Image(systemName: "person.2.fill")
                    .font(.title3).foregroundStyle(.tint)
                    .frame(width: 44, height: 44)
                    .glassEffect(.regular, in: .circle)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Your style").font(.headline)
                    Text("Turn your sessions into an Arena twin that plays like you.").font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
            }
            .padding(16)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

struct TwinView: View {
    @Environment(SessionStore.self) private var store
    @State private var state: Phase = .loading

    enum Phase {
        case loading
        case ready(StyleProfile, URL)
        case notEnough(String)
    }

    var body: some View {
        Screen {
            switch state {
            case .loading:
                ProgressView("Reading your sessions…").frame(maxWidth: .infinity).padding(.top, 40)
            case .notEnough(let why):
                ContentUnavailableView {
                    Label("Not enough play yet", systemImage: "figure.badminton")
                } description: {
                    Text(why)
                }
            case .ready(let p, let file):
                ready(p, file)
            }
        }
        .navigationTitle("Your style")
        .navigationBarTitleDisplayMode(.inline)
        .task { await compute() }
    }

    @ViewBuilder private func ready(_ p: StyleProfile, _ file: URL) -> some View {
        let b = StyleProfiler.build(p)
        Card("From your racquet", systemImage: "sensor.tag.radiowaves.forward") {
            StatGrid(minimum: 140) {
                BigStat(value: String(format: "%.0f", p.speed.p50Kmh), unit: "km/h", label: "Typical swing", style: .title)
                BigStat(value: String(format: "%.0f", p.speed.p90Kmh), unit: "km/h", label: "Hard swing", style: .title)
                BigStat(value: consistency(p.speed.cv), label: "Consistency", style: .title)
                BigStat(value: String(format: "%.1f", p.tempo.medianGapS), unit: "s", label: "Between shots", style: .title)
            }
            if p.speed.clippedFrac > 0.2 {
                Label("Your hardest swings exceed the sensor's range, so real speeds are higher than shown.", systemImage: "info.circle")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Text("\(p.source.swings) swings in \(p.source.sessions) session\(p.source.sessions == 1 ? "" : "s"), \(Int(p.source.minutes.rounded())) min. Speeds are estimates.")
                .font(.footnote).foregroundStyle(.secondary)
        }

        Card("Your twin's build", systemImage: "slider.horizontal.3") {
            buildRow("Power", b.power, "from your hard swings")
            buildRow("Speed", b.speed, "from your tempo")
            buildRow("Accuracy", b.accuracy, "from how steady your power swings are")
            buildRow("Reach", b.reach, "the remainder (the pod can't measure it)")
            Text("The Arena forces this build on a twin, then checks after 10 matches that it really plays at your tempo.")
                .font(.footnote).foregroundStyle(.secondary)
        }

        ShareLink(item: file, preview: SharePreview("my_style.json", image: Image(systemName: "doc.text"))) {
            Label("Export my_style.json", systemImage: "square.and.arrow.up")
        }
        .buttonStyle(.primary)

        Card("Train your twin", systemImage: "terminal") {
            Text("On your computer, with the Arena kit:").font(.subheadline)
            Text("oso-arena train --style my_style.json --minutes 60 --out runs/twin\noso-arena export runs/twin/best -o mytwin.safetensors --name \"My Twin\" --style my_style.json")
                .font(.caption.monospaced()).textSelection(.enabled)
                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            Text("Upload it on the Arena site under My models. The file holds only these numbers: no name, no pod ID, no raw data.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private func buildRow(_ name: String, _ v: Int, _ why: String) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 1) {
                Text(name).font(.subheadline.weight(.semibold))
                Text(why).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            Gauge(value: Double(v), in: 0...10) { EmptyView() }
                .gaugeStyle(.accessoryLinearCapacity).tint(Color.accentColor).frame(width: 90)
            Text("\(v)").font(.score(.headline)).frame(width: 28, alignment: .trailing)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(name) \(v) of 10, \(why)")
    }

    private func consistency(_ cv: Double) -> String { cv < 0.1 ? "Steady" : cv <= 0.2 ? "Mixed" : "Wild" }

    private func compute() async {
        let result = await store.styleProfile()
        let noPods = !store.sessions.contains { $0.source == .podStream || $0.source == .podUpload }
        switch result {
        case .success(let p):
            let file = FileManager.default.temporaryDirectory.appendingPathComponent("my_style.json")
            do {
                try Data(p.json().utf8).write(to: file, options: .atomic)
                state = .ready(p, file)
            } catch {
                state = .notEnough("Couldn't write the file: \(error.localizedDescription)")
            }
        case .failure(let e):
            let why = (e as? StyleProfileError)?.description ?? e.localizedDescription
            state = .notEnough(noPods ? "Record a few sessions with the racquet pod first. Your style is measured from the pod, not the watch." : why)
        }
    }
}

extension SessionStore {
    /// The style profile (also sent to the web dashboard by WebSync). Pod sessions only (the watch measures the wrist, not the
    /// racquet; the sample is synthetic). Parsed off the main actor.
    @MainActor
    func styleProfile() async -> Result<StyleProfile, Error> {
        let urls = sessions.filter { $0.source == .podStream || $0.source == .podUpload }.map { imuURL($0.id) }
        let created = ISO8601DateFormatter().string(from: Date())
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        return await Task.detached(priority: .userInitiated) {
            let inputs = urls.compactMap { u -> StyleProfiler.Input? in
                guard let d = try? Data(contentsOf: u), let cap = try? ImuFile.parse([UInt8](d)) else { return nil }
                return .init(parts: [cap.samples])
            }
            return Result { try StyleProfiler.compute(inputs, createdAt: created, tool: "Volante iOS \(version)", mount: .flat) }
        }.value
    }
}
