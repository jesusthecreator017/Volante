// Devices tab: the racquet pod, Apple Watch, Fitbit and the Raspberry Pi server, as a Settings-style form.

import SwiftUI

struct WearablesView: View {
    @Environment(PodLink.self) private var pod
    @Environment(Router.self) private var router
    @Environment(WatchReceiver.self) private var watch
    @Environment(FitbitLink.self) private var fitbit
    @Environment(PiServer.self) private var pi
    @State private var checking = false
    @AppStorage("oso.onboarded") private var onboarded = false

    var body: some View {
        @Bindable var pi = pi
        NavigationStack {
            Form {
                Section {
                    Button { router.tab = .pod } label: {
                        DeviceRow(icon: "sensor.tag.radiowaves.forward.fill", tint: .orange, title: "Racquet pod",
                                  status: podStatus, statusColor: pod.state == .connected ? Theme.good : .secondary, chevron: true)
                    }
                    .buttonStyle(.plain)
                    DeviceRow(icon: "applewatch", tint: .gray, title: "Apple Watch", status: watchStatus,
                              statusColor: watch.reachable ? Theme.good : watch.installed || watch.paired ? .orange : .secondary)
                    if let n = watch.liveSwings {
                        LabeledContent("Live swings", value: "\(n)")
                        if let bpm = watch.liveHeartRate { LabeledContent("Heart rate", value: String(format: "%.0f bpm", bpm)) }
                    }
                    if let last = watch.lastReceived { LabeledContent("Last watch session", value: last) }
                } header: {
                    Text("Sensors")
                } footer: {
                    Text("On the watch, open Volante and start a badminton workout. It records your wrist and heart rate and sends the session here when you end it.")
                }

                Section {
                    DeviceRow(icon: "heart.fill", tint: .pink, title: "Fitbit", status: fitbitStatus,
                              statusColor: fitbit.connected == true ? Theme.good : .secondary)
                    switch fitbit.connected {
                    case true?:
                        Button("Disconnect Fitbit", role: .destructive) { Task { await fitbit.disconnect() } }
                    case false?:
                        Button("Connect with Google") { Task { await fitbit.connect() } }
                            .disabled(fitbit.busy || !fitbit.configured)
                    case nil:
                        EmptyView()
                    }
                    if let e = fitbit.lastError { Text(e).font(.footnote).foregroundStyle(Theme.bad) }
                } header: {
                    Text("Heart rate")
                } footer: {
                    Text(fitbit.connected == nil
                         ? "Fitbit runs through your Pi server; connect to it below first."
                         : !fitbit.configured ? "Set GOOGLE_CLIENT_ID in Config.xcconfig (apple/README.md)."
                         : "Sign in once. Your Pi keeps the token and adds heart rate to your sessions.")
                }

                Section {
                    DeviceRow(icon: "server.rack", tint: .blue, title: "Raspberry Pi", status: piStatus,
                              statusColor: pi.reachable == true ? Theme.good : pi.reachable == false ? Theme.bad : .secondary,
                              busy: pi.checking || checking)
                    TextField("http://raspberrypi.local:8080", text: $pi.baseURL)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .onSubmit(check)
                    Button("Check connection", action: check).disabled(pi.checking || checking)
                } header: {
                    Text("Server")
                } footer: {
                    Text("Your Pi stores sessions, builds 3D replays and fetches Fitbit heart rate. Use its http address on the same Wi-Fi, or its IP if .local doesn't resolve.")
                }

                Section {
                    Button("Show the intro again") { onboarded = false }
                } footer: {
                    Text("Volante \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")")
                }
            }
            .navigationTitle("Devices")
            .task { await pi.checkHealth(); await fitbit.refreshStatus() }
            .refreshable { await pi.checkHealth(); await fitbit.refreshStatus() }
        }
    }

    private func check() {
        Task { checking = true; await pi.checkHealth(); await fitbit.refreshStatus(); checking = false }
    }

    private var podStatus: String {
        switch pod.state {
        case .connected: pod.status?.sleeping == true ? "Asleep" : "Connected"
        case .scanning: "Searching…"
        case .connecting: "Connecting…"
        default: "Not connected"
        }
    }
    private var watchStatus: String {
        watch.reachable ? "Connected" : watch.installed ? "Installed, not reachable" : watch.paired ? "Volante not installed" : "Not paired"
    }
    private var fitbitStatus: String {
        switch fitbit.connected { case true?: "Connected"; case false?: "Not connected"; case nil: "Needs the Pi" }
    }
    private var piStatus: String {
        switch pi.reachable { case true?: "Connected"; case false?: "Unreachable"; case nil: "Not checked" }
    }
}

/// An icon tile, a title and a status line, like a row in Settings.
private struct DeviceRow: View {
    let icon: String
    let tint: Color
    let title: String
    let status: String
    var statusColor: Color = .secondary
    var busy = false
    var chevron = false

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.body.weight(.semibold)).foregroundStyle(.white)
                .frame(width: 30, height: 30)
                .background(tint.gradient, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
                .accessibilityHidden(true)
            Text(title)
            Spacer(minLength: 8)
            if busy { ProgressView().controlSize(.small) } else {
                HStack(spacing: 5) {
                    Circle().fill(statusColor).frame(width: 7, height: 7).accessibilityHidden(true)
                    Text(status).foregroundStyle(.secondary)
                }
                .font(.subheadline)
            }
            if chevron { Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary) }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
