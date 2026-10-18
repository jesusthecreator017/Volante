// Play tab: find and connect the pod, then one clear action per state (live session, record on the pod, upload).
// Pod health and housekeeping (sleep, disconnect, firmware) live behind the toolbar menu and the Pod info sheet.

import OsoKit
import SwiftUI

struct PodView: View {
    @Environment(PodLink.self) private var pod
    @Environment(SessionStore.self) private var store
    @Environment(Router.self) private var router
    @Environment(\.openURL) private var openURL
    @State private var confirm: CheckedContinuation<Bool, Never>?
    @State private var showInfo = false
    @State private var confirmStop = false

    var body: some View {
        NavigationStack {
            Screen {
                if let e = pod.lastError { notice(e, systemImage: "exclamationmark.triangle.fill", color: Theme.bad) }
                if let n = pod.notice { notice(n, systemImage: "info.circle.fill", color: .secondary) }
                switch pod.state {
                case .connected: connectedContent
                default: connectContent
                }
            }
            .navigationTitle("Play")
            .toolbar { if pod.state == .connected { ToolbarItem(placement: .topBarTrailing) { podMenu } } }
            .sheet(isPresented: $showInfo) { PodInfoSheet() }
            .alert("Overwrite the stored session?", isPresented: .constant(confirm != nil)) {
                Button("Erase and record", role: .destructive) { confirm?.resume(returning: true); confirm = nil }
                Button("Keep it", role: .cancel) { confirm?.resume(returning: false); confirm = nil }
            } message: {
                Text("The pod holds a session that was not uploaded yet.")
            }
            .onAppear {
                pod.confirmOverwrite = { await withCheckedContinuation { k in confirm = k } }
            }
        }
    }

    private func notice(_ text: String, systemImage: String, color: Color) -> some View {
        Label(text, systemImage: systemImage)
            .font(.footnote).foregroundStyle(color)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private var podMenu: some View {
        Menu {
            Button { showInfo = true } label: { Label("Pod info", systemImage: "info.circle") }
            if pod.streaming { Button { pod.send(.syncMark) } label: { Label("Mark sync event", systemImage: "flag") } }
            Divider()
            if pod.status?.sleeping == false {
                Button { pod.sleep() } label: { Label("Put pod to sleep", systemImage: "moon.zzz") }
            }
            Button(role: .destructive) { pod.disconnect() } label: { Label("Disconnect", systemImage: "xmark.circle") }
        } label: {
            Label("Pod", systemImage: "ellipsis")
        }
    }

    // MARK: not connected

    @ViewBuilder private var connectContent: some View {
        VStack(spacing: 14) {
            PodOrb(active: pod.state == .scanning || pod.state == .connecting)
            Text(heroTitle).font(.system(.title, design: .rounded, weight: .bold)).multilineTextAlignment(.center)
            Text(heroText).font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding(.top, 24).padding(.bottom, 8).padding(.horizontal, 12)

        VStack(spacing: 12) {
            switch pod.access {
            case .notDetermined:
                Button { pod.enableBluetooth() } label: { Label("Allow Bluetooth", systemImage: "bolt.horizontal.circle") }.buttonStyle(.primary)
            case .denied:
                Button { if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) } } label: { Label("Open Settings", systemImage: "gearshape") }
                    .buttonStyle(.primary)
            case .poweredOff, .unsupported, .checking:
                EmptyView()
            case .ready:
                readyContent
            }
            Button { router.openSample(store) } label: { Label("Try the sample session", systemImage: "play.circle") }
                .buttonStyle(.secondary)
        }
    }

    private var heroTitle: String {
        switch pod.access {
        case .notDetermined: "Connect your pod"
        case .denied: "Bluetooth is blocked"
        case .poweredOff: "Turn on Bluetooth"
        case .unsupported: "No Bluetooth"
        case .checking: "Checking Bluetooth…"
        case .ready:
            switch pod.state {
            case .scanning: pod.found.isEmpty ? "Looking for your pod…" : "Pod found"
            case .connecting: "Connecting…"
            default: "Connect your pod"
            }
        }
    }

    private var heroText: String {
        switch pod.access {
        case .notDetermined: "Volante uses Bluetooth to find the pod on your racquet. iOS asks once."
        case .denied: "Allow Bluetooth for Volante in Settings to find your pod."
        case .poweredOff: "Open Control Center and tap Bluetooth. Volante finds your pod as soon as it's on."
        case .unsupported: "This device can't talk to the pod."
        case .checking: " "
        case .ready:
            pod.state == .scanning
                ? "A sleeping pod wakes up every 30 seconds, so this can take a moment."
                : "Clip the pod to your racquet and turn it on. Every swing gets its head speed, stroke and timing."
        }
    }

    @ViewBuilder private var readyContent: some View {
        switch pod.state {
        case .scanning:
            if pod.found.isEmpty {
                Button {} label: { HStack(spacing: 10) { ProgressView(); Text("Scanning") } }
                    .buttonStyle(.secondary).disabled(true)
            }
            ForEach(pod.found, id: \.identifier) { p in
                Button { pod.connect(p) } label: { Label("Connect \(p.name ?? "Volante pod")", systemImage: "sensor.tag.radiowaves.forward") }
                    .buttonStyle(.primary)
            }
        case .connecting:
            Button {} label: { HStack(spacing: 10) { ProgressView(); Text("Connecting") } }
                .buttonStyle(.secondary).disabled(true)
        default:
            Button { pod.scan() } label: { Label("Find my pod", systemImage: "dot.radiowaves.left.and.right") }
                .buttonStyle(.primary)
        }
    }

    // MARK: connected

    @ViewBuilder private var connectedContent: some View {
        PodHeader(showInfo: $showInfo)
        if let st = pod.status {
            if st.sleeping {
                Card {
                    Label("Your pod is asleep", systemImage: "moon.zzz.fill").font(.headline)
                    Text("Scan and connect to wake it. It checks in every 30 seconds and lasts about 70 hours asleep.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            } else if pod.streaming, let live = pod.live {
                livePanel(live)
            } else if pod.uploading {
                uploadCard
            } else if st.recording || st.erasing {
                recordingCard(st)
            } else {
                startCards(st)
            }
        }
    }

    private func livePanel(_ live: LiveAnalyzer) -> some View {
        Card {
            HStack {
                Label("Live", systemImage: "dot.radiowaves.left.and.right").font(.headline).foregroundStyle(Theme.good)
                    .symbolEffect(.variableColor.iterative, options: .repeating)
                Spacer()
                Text(String(format: "%.0f°/s", live.currentDps)).font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
            }
            if !live.calibrated {
                Label("Hold the racquet still for a second…", systemImage: "hand.raised.fill").font(.subheadline).foregroundStyle(.orange)
            }
            VStack(spacing: 0) {
                Text("\(live.swingCount)").font(.system(size: 88, weight: .heavy, design: .rounded).monospacedDigit())
                    .contentTransition(.numericText(value: Double(live.swingCount)))
                    .animation(.snappy, value: live.swingCount)
                Text("swings").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity)
            .accessibilityElement(children: .combine)
            HStack {
                BigStat(value: pod.liveSwings.last.map { String(format: "%.1f", $0.headSpeed) } ?? "–", unit: "m/s", label: "Last", style: .title2)
                BigStat(value: live.bestSpeed.map { String(format: "%.1f", $0) } ?? "–", unit: "m/s", label: "Peak", style: .title2)
                BigStat(value: "\(live.hitCount)", label: "Hits", style: .title2)
            }
            if !pod.liveSwings.isEmpty {
                Divider()
                ForEach(pod.liveSwings.suffix(4).reversed()) { s in
                    HStack {
                        Circle().fill(s.impact ? Theme.hot : Theme.group(.unclassified)).frame(width: 10, height: 10).accessibilityHidden(true)
                        Text(s.impact ? "Hit" : "No contact").font(.subheadline.weight(.semibold))
                        Spacer(minLength: 8)
                        Text(String(format: "%.1f m/s", s.headSpeed)).font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    .transition(.move(edge: .top).combined(with: .opacity))
                }
                .animation(.snappy, value: pod.liveSwings.count)
            }
            Button { confirmStop = true } label: { Label("End session", systemImage: "stop.fill") }
                .buttonStyle(.primary)
                .padding(.top, 4)
                .confirmationDialog("End this session?", isPresented: $confirmStop, titleVisibility: .visible) {
                    Button("Save session") { pod.stopStream(save: true); router.tab = .sessions }
                    Button("Discard", role: .destructive) { pod.stopStream(save: false) }
                }
        }
        .sensoryFeedback(.impact(weight: .light), trigger: live.swingCount)
    }

    private var uploadCard: some View {
        Card("Downloading from the pod", systemImage: "arrow.down.circle") {
            ProgressView(value: Double(pod.uploadReceived), total: Double(max(1, pod.uploadExpected)))
            Text("\(pod.uploadReceived) of \(pod.uploadExpected) packets").font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
            Button("Finish now and keep what arrived") { pod.finishUploadEarly() }.buttonStyle(.secondary)
        }
    }

    private func recordingCard(_ st: PodStatus) -> some View {
        Card {
            Label(st.erasing ? "Preparing the pod…" : "Recording on the pod", systemImage: "record.circle")
                .font(.headline).foregroundStyle(Theme.bad)
                .symbolEffect(.pulse, options: .repeating, isActive: st.recording)
            Text("Play as long as you like (up to about 12 minutes). Your phone doesn't need to stay close.")
                .font(.subheadline).foregroundStyle(.secondary)
            Button { pod.send(.stopRecording) } label: { Label("Stop recording", systemImage: "stop.fill") }
                .buttonStyle(.primary)
        }
    }

    @ViewBuilder private func startCards(_ st: PodStatus) -> some View {
        ModeTile(icon: "waveform.path.ecg", title: "Live session",
                 text: "See every swing on your phone as you hit. Keep it courtside.") {
            pod.startStream(settings: store.settings)
        }
        .buttonStyle(.primary)
        ModeTile(icon: "record.circle", title: "Record on the pod",
                 text: "The pod records by itself. Download it when you're done.") {
            Task { await pod.startRecording() }
        }
        .buttonStyle(.secondary)
        if st.bufferedPackets > 0 {
            Button { pod.startUpload() } label: {
                Label("Download stored session (\(clock(Double(st.bufferedPackets) / Double(max(1, st.rateHz)))))", systemImage: "arrow.down.circle")
            }
            .buttonStyle(.secondary)
        }
    }
}

/// A large two-line action button for the session modes.
private struct ModeTile: View {
    let icon: String
    let title: String
    let text: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: icon).font(.title2.weight(.semibold)).frame(width: 34)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.headline)
                    Text(text).font(.footnote).opacity(0.8)
                }
                .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.footnote.weight(.bold)).opacity(0.6)
            }
            .padding(.vertical, 6)
        }
    }
}

/// The pod's glass orb on the connect screen; it breathes while scanning.
private struct PodOrb: View {
    var active: Bool
    var body: some View {
        Image(systemName: "sensor.tag.radiowaves.forward.fill")
            .font(.system(size: 52, weight: .semibold))
            .foregroundStyle(.tint)
            .symbolEffect(.variableColor.iterative, options: .repeating, isActive: active)
            .frame(width: 128, height: 128)
            .glassEffect(.regular, in: .circle)
            .accessibilityHidden(true)
    }
}

/// Connected pod: name, state and glanceable health.
private struct PodHeader: View {
    @Environment(PodLink.self) private var pod
    @Binding var showInfo: Bool

    var body: some View {
        Button { showInfo = true } label: {
            HStack(spacing: 14) {
                Image(systemName: "sensor.tag.radiowaves.forward.fill")
                    .font(.title2).foregroundStyle(.tint)
                    .frame(width: 52, height: 52)
                    .glassEffect(.regular, in: .circle)
                VStack(alignment: .leading, spacing: 6) {
                    Text(pod.peripheralName ?? "Volante pod").font(.headline).foregroundStyle(.primary)
                    HStack(spacing: 6) {
                        StatusChip(text: stateText, systemImage: "circle.fill", color: stateColor)
                        if let b = pod.battery { StatusChip(text: "\(b)%", systemImage: battery(b)) }
                        if let r = pod.rssi { StatusChip(text: r > -65 ? "Strong" : r > -80 ? "Fair" : "Weak", systemImage: "antenna.radiowaves.left.and.right") }
                    }
                }
                Spacer(minLength: 0)
                Image(systemName: "info.circle").foregroundStyle(.secondary)
            }
            .padding(16)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityHint("Shows pod info")
    }

    private var stateText: String {
        guard let st = pod.status else { return "Connected" }
        if st.sleeping { return "Asleep" }
        if pod.streaming { return "Live" }
        if st.recording { return "Recording" }
        if st.imuOk == false { return "Sensor issue" }
        return "Ready"
    }
    private var stateColor: Color {
        guard let st = pod.status else { return Theme.good }
        if st.sleeping { return .secondary }
        if st.recording { return Theme.bad }
        if st.imuOk == false { return .orange }
        return Theme.good
    }
    private func battery(_ b: Int) -> String {
        b > 85 ? "battery.100" : b > 60 ? "battery.75" : b > 35 ? "battery.50" : b > 10 ? "battery.25" : "battery.0"
    }
}

/// Everything about the pod a player rarely needs: health, sensor ranges, the link.
private struct PodInfoSheet: View {
    @Environment(PodLink.self) private var pod
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                if let st = pod.status {
                    Section("Health") {
                        LabeledContent("Battery", value: pod.battery.map { "\($0)%" } ?? "Not measured")
                        LabeledContent("Sensor", value: st.imuOk ? (st.imuIs6500 ? "OK (MPU-6500)" : "OK") : "Not responding")
                        LabeledContent("Signal", value: pod.rssi.map { "\($0) dBm" } ?? "–")
                        LabeledContent("Stored on the pod", value: clock(Double(st.bufferedPackets) / Double(max(1, st.rateHz))))
                        if st.fifoOverflows > 0 { LabeledContent("Buffer overflows", value: "\(st.fifoOverflows)") }
                    }
                    Section("Device") {
                        LabeledContent("Name", value: pod.peripheralName ?? "Volante pod")
                        LabeledContent("Firmware", value: st.firmware)
                        LabeledContent("Ranges", value: "±\(st.accelG) g, ±\(st.gyroDps)°/s")
                        LabeledContent("Sample rate", value: "\(st.rateHz) Hz")
                        if let m = pod.mtu { LabeledContent("Link", value: "MTU \(m) B, \(packetsPerNotification(mtu: m)) packets each") }
                        if !st.stateLabels.isEmpty { LabeledContent("State", value: st.stateLabels.joined(separator: ", ")) }
                    }
                }
                Section {
                    if pod.status?.sleeping == false {
                        Button { pod.sleep(); dismiss() } label: { Label("Put pod to sleep", systemImage: "moon.zzz") }
                    }
                    Button(role: .destructive) { pod.disconnect(); dismiss() } label: { Label("Disconnect", systemImage: "xmark.circle") }
                } footer: {
                    Text("Asleep, the pod lasts about 70 hours and wakes when you connect.")
                }
            }
            .themedList()
            .navigationTitle("Pod info")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done", systemImage: "checkmark") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }
}
