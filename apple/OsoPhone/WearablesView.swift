// Apple Watch status and Fitbit connection.

import SwiftUI

struct WearablesView: View {
    @Environment(WatchReceiver.self) private var watch
    @Environment(FitbitLink.self) private var fitbit

    var body: some View {
        NavigationStack {
            List {
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
                    if fitbit.connected {
                        LabeledContent("Status", value: "connected")
                        Button("Disconnect", role: .destructive) { fitbit.disconnect() }
                    } else {
                        Button("Connect Fitbit (Google account)") { Task { await fitbit.connect() } }
                            .disabled(fitbit.busy || !fitbit.configured)
                        if !fitbit.configured { Text("Set GOOGLE_CLIENT_ID in Config.xcconfig (see apple/README.md).").font(.footnote) }
                    }
                    if let e = fitbit.lastError { Text(e).font(.footnote).foregroundStyle(.red) }
                } header: {
                    Text("Fitbit")
                } footer: {
                    Text("Fitbit shares heart rate (1 s), calories and workouts through the Google Health API; it has no motion data for apps. Open a session and tap Fetch from Fitbit to put your heart rate on its swings.")
                }
            }
            .navigationTitle("Wearables")
        }
    }
}
