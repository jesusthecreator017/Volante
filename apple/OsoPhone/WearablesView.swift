// Raspberry Pi server, Apple Watch status and Fitbit connection.

import SwiftUI

struct WearablesView: View {
    @Environment(WatchReceiver.self) private var watch
    @Environment(FitbitLink.self) private var fitbit
    @Environment(PiServer.self) private var pi

    var body: some View {
        @Bindable var pi = pi
        NavigationStack {
            List {
                Section {
                    TextField("http://raspberrypi.local:8080", text: $pi.baseURL)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    HStack {
                        Button("Check connection") { Task { await pi.checkHealth(); await fitbit.refreshStatus() } }
                        Spacer()
                        switch pi.reachable {
                        case true?: Text("connected").foregroundStyle(.green)
                        case false?: Text("unreachable").foregroundStyle(.red)
                        case nil: EmptyView()
                        }
                    }
                } header: {
                    Text("Raspberry Pi server")
                } footer: {
                    Text("Sessions, replays and Fitbit heart rate go through the oso server on the Pi (deploy/pi/README.md). Use its http port on the same Wi-Fi; if .local doesn't resolve, use the Pi's IP.")
                }

                Section {
                    LabeledContent("Paired", value: watch.paired ? "yes" : "no")
                    LabeledContent("oso installed on the watch", value: watch.installed ? "yes" : "no")
                    LabeledContent("Reachable", value: watch.reachable ? "yes" : "no")
                    if let n = watch.liveSwings {
                        LabeledContent("Live swings", value: "\(n)")
                        if let bpm = watch.liveHeartRate { LabeledContent("Heart rate", value: String(format: "%.0f bpm", bpm)) }
                    }
                    if let last = watch.lastReceived { LabeledContent("Last session", value: last) }
                } header: {
                    Text("Apple Watch")
                } footer: {
                    Text("Start a badminton workout in oso on the watch. It records wrist motion (200 Hz) and heart rate, counts swings on the wrist, and sends the session here when you end it.")
                }

                Section {
                    switch fitbit.connected {
                    case true?:
                        LabeledContent("Status", value: "connected (token on the Pi)")
                        Button("Disconnect", role: .destructive) { Task { await fitbit.disconnect() } }
                    case false?:
                        Button("Connect Fitbit (Google account)") { Task { await fitbit.connect() } }
                            .disabled(fitbit.busy || !fitbit.configured)
                        if !fitbit.configured { Text("Set GOOGLE_CLIENT_ID in Config.xcconfig (see apple/README.md).").font(.footnote) }
                    case nil:
                        Text("Connect to the Pi first: Fitbit runs through it.").font(.footnote)
                    }
                    if let e = fitbit.lastError { Text(e).font(.footnote).foregroundStyle(.red) }
                } header: {
                    Text("Fitbit")
                } footer: {
                    Text("Sign in once here; the Pi keeps the Google token and fetches heart rate (1 s) from the Google Health API. Fitbit has no motion data for apps. Open a session and tap Fetch from Fitbit to put your heart rate on its swings.")
                }
            }
            .navigationTitle("Connections")
            .task { await pi.checkHealth(); await fitbit.refreshStatus() }
        }
    }
}
