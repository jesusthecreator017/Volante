// Pod screen: connect, device health, live swings, on-device recording + upload, sleep.

import OsoKit
import SwiftUI

struct PodView: View {
    @Environment(PodLink.self) private var pod
    @Environment(SessionStore.self) private var store
    @Environment(Router.self) private var router
    @Environment(\.openURL) private var openURL
    @State private var confirm: CheckedContinuation<Bool, Never>?

    var body: some View {
        NavigationStack {
            Screen {
                switch pod.state {
                case .connected: connectedContent
                default: connectContent
                }
                if let n = pod.notice { Card { Text(n).font(.footnote) } }
                if let e = pod.lastError { Card { Label(e, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(Theme.bad) } }
            }
            .navigationTitle("Racquet pod")
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

    // MARK: not connected

    @ViewBuilder private var connectContent: some View {
        Banner {
            Image(systemName: "figure.badminton").font(.system(size: 54, weight: .semibold)).accessibilityHidden(true)
            Text("Your swing, measured.").font(.system(.title, design: .rounded, weight: .heavy))
            Text("The oso pod clips to your racquet and counts every swing, with head speed, stroke type and impact timing, over Bluetooth.")
                .font(.subheadline).foregroundStyle(.white.opacity(0.85))
        }
        Card {
            switch pod.access {
            case .notDetermined:
                Label("Allow Bluetooth", systemImage: "bolt.horizontal.circle").font(.headline).foregroundStyle(Theme.courtInk)
                Text("oso needs Bluetooth to find your pod. iOS will ask once.").font(.subheadline).foregroundStyle(.secondary)
                Button { pod.enableBluetooth() } label: { Label("Allow Bluetooth", systemImage: "bolt.horizontal.circle") }
                    .buttonStyle(.primary)
            case .denied:
                Label("Bluetooth is blocked", systemImage: "xmark.circle.fill").font(.headline).foregroundStyle(Theme.bad)
                Text("Turn on Bluetooth for oso in Settings to find your pod.").font(.subheadline).foregroundStyle(.secondary)
                Button { if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) } } label: { Label("Open Settings", systemImage: "gearshape") }
                    .buttonStyle(.primary)
            case .poweredOff:
                Label("Turn on Bluetooth", systemImage: "bolt.horizontal.circle").font(.headline).foregroundStyle(Theme.bad)
                Text("Bluetooth is off. Open Control Center and tap Bluetooth; oso finds your pod as soon as it is on.").font(.subheadline).foregroundStyle(.secondary)
            case .unsupported:
                Label("Bluetooth isn't available on this device.", systemImage: "bolt.horizontal.circle").font(.subheadline)
            case .checking:
                HStack(spacing: 10) { ProgressView(); Text("Checking Bluetooth…").font(.headline) }
            case .ready:
                readyContent
            }
            Button { router.openSample(store) } label: { Label("Try the sample session", systemImage: "play.circle") }
                .buttonStyle(.secondary)
        }
    }

    @ViewBuilder private var readyContent: some View {
        switch pod.state {
        case .scanning:
            HStack(spacing: 10) { ProgressView(); Text("Looking for your pod…").font(.headline) }
            Text("A sleeping pod advertises for 2 s every 30 s, so this can take up to half a minute.")
                .font(.footnote).foregroundStyle(.secondary)
            ForEach(pod.found, id: \.identifier) { p in
                Button { pod.connect(p) } label: { Label(p.name ?? "oso pod", systemImage: "sensor.tag.radiowaves.forward") }
                    .buttonStyle(.primary)
            }
        case .connecting:
            HStack(spacing: 10) { ProgressView(); Text("Connecting…").font(.headline) }
        default:
            Button { pod.scan() } label: { Label("Scan for pods", systemImage: "dot.radiowaves.left.and.right") }
                .buttonStyle(.primary)
        }
    }

    // MARK: connected

    @ViewBuilder private var connectedContent: some View {
        Banner {
            HStack {
                Label(pod.peripheralName ?? "oso pod", systemImage: "sensor.tag.radiowaves.forward").font(.headline)
                Spacer(minLength: 8)
                StatusChip(text: pod.status?.sleeping == true ? "Sleeping" : "Connected", systemImage: "checkmark.circle.fill", color: .white)
            }
            if let st = pod.status { Text(st.stateLabels.joined(separator: " · ").capitalized).font(.subheadline).foregroundStyle(.white.opacity(0.85)) }
        }
        if let st = pod.status { health(st) }
        if pod.status?.sleeping == false {
            liveCard
            recordCard
            Card("Sleep", systemImage: "moon.zzz") {
                Button(role: .destructive) { pod.sleep() } label: { Label("Put pod to sleep", systemImage: "moon.fill") }
                    .buttonStyle(.secondaryDestructive)
                Text("Stops recording and parks the pod (about 70 h). To wake it, scan and connect: it advertises 2 s every 30 s. (Command 0x08)")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        if let st = pod.status { details(st) }
        Button("Disconnect") { pod.disconnect() }.buttonStyle(.secondary)
    }

    private func health(_ st: PodStatus) -> some View {
        Card("Device health", systemImage: "heart.text.square") {
            StatGrid(minimum: 130) {
                BigStat(value: pod.battery.map { "\($0)" } ?? "–", unit: pod.battery == nil ? nil : "%", label: pod.battery == nil ? "Battery (no divider fitted)" : "Battery", style: .title)
                BigStat(value: st.imuOk ? "OK" : "Down", label: st.imuOk ? (st.imuIs6500 ? "IMU (MPU-6500)" : "IMU") : "IMU not responding", style: .title)
                BigStat(value: signal, label: pod.rssi.map { "Signal (\($0) dBm)" } ?? "Signal", style: .title)
                BigStat(value: st.sleeping ? "–" : "\(Int(st.bufferedPackets) / max(1, Int(st.rateHz)))", unit: st.sleeping ? nil : "s", label: "Stored on the pod", style: .title)
            }
            if st.sleeping {
                Text("The pod is asleep. Scan and connect to wake it (it advertises 2 s every 30 s).").font(.footnote).foregroundStyle(.secondary)
            }
            if st.fifoOverflows > 0 {
                Label("\(st.fifoOverflows) sensor buffer overflows", systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.orange)
            }
        }
    }

    private var signal: String {
        guard let r = pod.rssi else { return "–" }
        return r > -65 ? "Strong" : r > -80 ? "Fair" : "Weak"
    }

    private func details(_ st: PodStatus) -> some View {
        Card("Details", systemImage: "info.circle") {
            VStack(spacing: 8) {
                LabeledContent("Firmware", value: st.firmware)
                LabeledContent("Ranges", value: "±\(st.accelG) g, ±\(st.gyroDps) dps @ \(st.rateHz) Hz")
                if let m = pod.mtu { LabeledContent("Link", value: "MTU \(m) B, \(packetsPerNotification(mtu: m)) packets/notification") }
            }
            .font(.footnote)
        }
    }

    @ViewBuilder private var liveCard: some View {
        Card("Live swings", systemImage: "waveform.path.ecg") {
            if pod.streaming, let live = pod.live {
                if !live.calibrated {
                    Label("Hold the racquet still for 1 s to calibrate…", systemImage: "hand.raised").font(.subheadline).foregroundStyle(.orange)
                }
                StatGrid(minimum: 110) {
                    BigStat(value: "\(live.swingCount)", label: "Swings (\(live.hitCount) hits)", style: .largeTitle)
                    BigStat(value: pod.liveSwings.last.map { String(format: "%.1f", $0.headSpeed) } ?? "–", unit: "m/s", label: "Last swing", style: .largeTitle)
                    BigStat(value: live.bestSpeed.map { String(format: "%.1f", $0) } ?? "–", unit: "m/s", label: "Peak", style: .largeTitle)
                }
                Text(String(format: "Now: %.0f dps", live.currentDps)).font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                ForEach(pod.liveSwings.suffix(5).reversed()) { s in
                    HStack {
                        Circle().fill(Theme.stroke(s.stroke)).frame(width: 10, height: 10).accessibilityHidden(true)
                        Text("#\(s.index) \(s.stroke.rawValue)").font(.subheadline.weight(.semibold))
                        Spacer(minLength: 8)
                        Text(String(format: "%.1f m/s%@", s.headSpeed, s.impact ? " · hit" : "")).font(.subheadline.monospacedDigit())
                    }
                }
                Button { pod.send(.syncMark) } label: { Label("Mark sync event", systemImage: "flag") }.buttonStyle(.secondary)
                Button { pod.stopStream(save: true) } label: { Label("Stop and save", systemImage: "stop.circle.fill") }.buttonStyle(.primary)
                Button(role: .destructive) { pod.stopStream(save: false) } label: { Label("Stop and discard", systemImage: "trash") }.buttonStyle(.secondaryDestructive)
            } else {
                Text("Stream swings to your phone as you hit, and save the session when you stop.").font(.subheadline).foregroundStyle(.secondary)
                Button { pod.startStream(settings: store.settings) } label: { Label("Start live session", systemImage: "play.fill") }
                    .buttonStyle(.primary)
                    .disabled(pod.uploading)
            }
        }
    }

    @ViewBuilder private var recordCard: some View {
        Card("On-device recording", systemImage: "internaldrive") {
            if pod.uploading {
                ProgressView(value: Double(pod.uploadReceived), total: Double(max(1, pod.uploadExpected))) {
                    Text("Uploading \(pod.uploadReceived) / \(pod.uploadExpected) packets").font(.subheadline)
                }
                .tint(.accentColor)
                Button("Finish now (keep what arrived)") { pod.finishUploadEarly() }.buttonStyle(.secondary)
            } else {
                let st = pod.status
                Button { Task { await pod.startRecording() } } label: { Label("Start recording", systemImage: "record.circle") }
                    .buttonStyle(.primary)
                    .disabled(st?.recording == true || st?.erasing == true)
                Button { pod.send(.stopRecording) } label: { Label("Stop recording", systemImage: "stop.circle") }
                    .buttonStyle(.secondary)
                    .disabled(st?.recording != true && st?.erasing != true)
                Button { pod.startUpload() } label: { Label("Upload stored session", systemImage: "arrow.down.circle") }
                    .buttonStyle(.secondary)
                    .disabled(st?.erasing == true || pod.streaming)
            }
            Text("The pod records about 12.5 min to flash, by itself at power-on unless it holds a session that wasn't uploaded. A complete upload is confirmed to the pod. (Commands 0x03, 0x04, 0x05, 0x07)")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }
}
