// Raspberry Pi server, Apple Watch status and Fitbit connection.

import SwiftUI

struct WearablesView: View {
    @Environment(WatchReceiver.self) private var watch
    @Environment(FitbitLink.self) private var fitbit
    @Environment(PiServer.self) private var pi
    @State private var checking = false
    @AppStorage("oso.onboarded") private var onboarded = false

    var body: some View {
        @Bindable var pi = pi
        NavigationStack {
            Screen {
                Card("Raspberry Pi server", systemImage: "server.rack") {
                    if pi.checking || checking {
                        HStack(spacing: 6) { ProgressView().controlSize(.small); Text("Checking…").font(.caption.weight(.semibold)) }
                            .padding(.horizontal, 10).padding(.vertical, 5)
                            .background(Color.secondary.opacity(0.14), in: Capsule())
                    } else { chip(for: pi.reachable) }
                    TextField("http://raspberrypi.local:8080", text: $pi.baseURL)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .padding(12)
                        .background(Theme.canvas, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    Button {
                        Task { checking = true; await pi.checkHealth(); await fitbit.refreshStatus(); checking = false }
                    } label: { Label("Check connection", systemImage: "arrow.clockwise") }
                        .buttonStyle(.primary)
                        .disabled(pi.checking || checking)
                    foot("Sessions, replays and Fitbit heart rate go through the oso server on the Pi (deploy/pi/README.md). Use its http port on the same Wi-Fi; if .local doesn't resolve, use the Pi's IP.")
                }

                Card("Apple Watch", systemImage: "applewatch") {
                    HStack(spacing: 8) {
                        if watch.reachable { StatusChip(text: "Connected", systemImage: "checkmark.circle.fill", color: Theme.good) }
                        else if watch.installed { StatusChip(text: "Installed, not reachable", systemImage: "applewatch", color: .orange) }
                        else if watch.paired { StatusChip(text: "Paired, oso not installed", systemImage: "applewatch", color: .orange) }
                        else { StatusChip(text: "Not paired", systemImage: "xmark.circle", color: .secondary) }
                    }
                    if let n = watch.liveSwings {
                        StatGrid(minimum: 110) {
                            BigStat(value: "\(n)", label: "Live swings", style: .title)
                            if let bpm = watch.liveHeartRate { BigStat(value: String(format: "%.0f", bpm), unit: "bpm", label: "Heart rate", style: .title) }
                        }
                    }
                    if let last = watch.lastReceived { LabeledContent("Last session", value: last).font(.subheadline) }
                    foot("Start a badminton workout in oso on the watch. It records wrist motion (200 Hz) and heart rate, counts swings on the wrist, and sends the session here when you end it.")
                }

                Card("Fitbit", systemImage: "heart.circle") {
                    switch fitbit.connected {
                    case true?:
                        StatusChip(text: "Connected (token on the Pi)", systemImage: "checkmark.circle.fill", color: Theme.good)
                        Button(role: .destructive) { Task { await fitbit.disconnect() } } label: { Text("Disconnect") }
                            .buttonStyle(.secondaryDestructive)
                    case false?:
                        StatusChip(text: "Not connected", systemImage: "xmark.circle", color: .secondary)
                        Button { Task { await fitbit.connect() } } label: { Label("Connect Fitbit (Google account)", systemImage: "link") }
                            .buttonStyle(.primary)
                            .disabled(fitbit.busy || !fitbit.configured)
                        if !fitbit.configured { foot("Set GOOGLE_CLIENT_ID in Config.xcconfig (see apple/README.md).") }
                    case nil:
                        StatusChip(text: "Needs the Pi", systemImage: "questionmark.circle", color: .secondary)
                        foot("Connect to the Pi first: Fitbit runs through it.")
                    }
                    if let e = fitbit.lastError { Text(e).font(.footnote).foregroundStyle(Theme.bad) }
                    foot("Sign in once here; the Pi keeps the Google token and fetches heart rate (1 s) from the Google Health API. Fitbit has no motion data for apps. Open a session and tap Fetch from Fitbit to put your heart rate on its swings.")
                }

                Card("Welcome", systemImage: "hand.wave") {
                    Button { onboarded = false } label: { Label("Show intro again", systemImage: "arrow.counterclockwise") }
                        .buttonStyle(.secondary)
                    foot("Replays the first-launch tour: the pod, Bluetooth and the optional Pi.")
                }
            }
            .navigationTitle("Connections")
            .task { await pi.checkHealth(); await fitbit.refreshStatus() }
        }
    }

    @ViewBuilder private func chip(for reachable: Bool?) -> some View {
        switch reachable {
        case true?: StatusChip(text: "Connected", systemImage: "checkmark.circle.fill", color: Theme.good)
        case false?: StatusChip(text: "Unreachable", systemImage: "exclamationmark.circle.fill", color: Theme.bad)
        case nil: StatusChip(text: "Not checked", systemImage: "questionmark.circle", color: .secondary)
        }
    }

    private func foot(_ text: String) -> some View {
        Text(text).font(.footnote).foregroundStyle(.secondary)
    }
}
