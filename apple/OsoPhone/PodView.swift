// Pod screen: connect, state, live swings, on-device recording + upload, sleep.

import OsoKit
import SwiftUI

struct PodView: View {
    @Environment(PodLink.self) private var pod
    @Environment(SessionStore.self) private var store
    @State private var confirm: CheckedContinuation<Bool, Never>?

    var body: some View {
        NavigationStack {
            List {
                connection
                if pod.state == .connected, let st = pod.status { device(st) }
                if pod.state == .connected, pod.status?.sleeping == false {
                    liveSection
                    recordSection
                    Section {
                        Button("Put pod to sleep (0x08)", role: .destructive) { pod.sleep() }
                    } footer: {
                        Text("Stops recording and parks the pod (~70 h). To wake it, scan and connect: it advertises 2 s every 30 s.")
                    }
                }
                if let n = pod.notice { Section { Text(n).font(.footnote) } }
                if let e = pod.lastError { Section { Text(e).font(.footnote).foregroundStyle(.red) } }
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

    @ViewBuilder private var connection: some View {
        Section("Connection") {
            switch pod.state {
            case .off: Text("Bluetooth is off or not allowed")
            case .idle: Button("Scan for pods") { pod.scan() }
            case .scanning:
                HStack { ProgressView(); Text("Scanning (a sleeping pod can take 30 s)…") }
                ForEach(pod.found, id: \.identifier) { p in
                    Button(p.name ?? "oso pod") { pod.connect(p) }
                }
            case .connecting: HStack { ProgressView(); Text("Connecting…") }
            case .connected:
                LabeledContent("Pod", value: pod.peripheralName ?? "oso")
                Button("Disconnect") { pod.disconnect() }
            }
        }
    }

    private func device(_ st: PodStatus) -> some View {
        Section("Device") {
            LabeledContent("State", value: st.stateLabels.joined(separator: ", "))
            LabeledContent("Firmware", value: st.firmware)
            LabeledContent("Battery", value: pod.battery.map { "\($0) %" } ?? "unknown (no divider fitted)")
            LabeledContent("IMU", value: st.imuOk ? (st.imuIs6500 ? "ok (MPU-6500)" : "ok") : "not responding")
            LabeledContent("Ranges", value: "±\(st.accelG) g, ±\(st.gyroDps) dps @ \(st.rateHz) Hz")
            LabeledContent("Stored", value: st.sleeping
                ? "sleeping (session kept; wake to see it)"
                : "\(st.bufferedPackets) packets (\(Int(st.bufferedPackets) / max(1, Int(st.rateHz))) s)")
            if st.fifoOverflows > 0 { LabeledContent("FIFO overflows", value: "\(st.fifoOverflows)") }
            if let r = pod.rssi { LabeledContent("RSSI", value: "\(r) dBm") }
            if let m = pod.mtu { LabeledContent("MTU", value: "\(m) B → \(packetsPerNotification(mtu: m)) packets/notification") }
        }
    }

    @ViewBuilder private var liveSection: some View {
        Section("Live") {
            if pod.streaming, let live = pod.live {
                if !live.calibrated { Text("Hold the racquet still for 1 s to calibrate…").foregroundStyle(.orange) }
                LabeledContent("Swings / hits", value: "\(live.swingCount) / \(live.hitCount)")
                LabeledContent("|ω| now", value: String(format: "%.0f dps", live.currentDps))
                if let b = live.bestSpeed { LabeledContent("Best head speed", value: String(format: "%.1f m/s", b)) }
                ForEach(pod.liveSwings.suffix(5).reversed()) { s in
                    LabeledContent("Swing \(s.index)", value: String(format: "%.0f dps · %.1f m/s%@", s.peakDps, s.headSpeed, s.impact ? " · hit" : ""))
                }
                Button("Mark sync event (0x06)") { pod.send(.syncMark) }
                Button("Stop & save") { pod.stopStream(save: true) }
                Button("Stop & discard", role: .destructive) { pod.stopStream(save: false) }
            } else {
                Button("Start live session") { pod.startStream(settings: store.settings) }
                    .disabled(pod.uploading)
            }
        }
    }

    @ViewBuilder private var recordSection: some View {
        Section {
            if pod.uploading {
                ProgressView(value: Double(pod.uploadReceived), total: Double(max(1, pod.uploadExpected))) {
                    Text("Uploading \(pod.uploadReceived) / \(pod.uploadExpected) packets")
                }
                Button("Finish now (keep what arrived)") { pod.finishUploadEarly() }
            } else {
                let st = pod.status
                Button("Start recording (0x03)") { Task { await pod.startRecording() } }
                    .disabled(st?.recording == true || st?.erasing == true)
                Button("Stop recording (0x04)") { pod.send(.stopRecording) }
                    .disabled(st?.recording != true && st?.erasing != true)
                Button("Upload stored session (0x05)") { pod.startUpload() }
                    .disabled(st?.erasing == true || pod.streaming)
            }
        } header: {
            Text("On-device recording")
        } footer: {
            Text("The pod records ~12.5 min to flash, by itself at power-on unless it holds a session that wasn't uploaded. A complete upload is confirmed to the pod (0x07).")
        }
    }
}
