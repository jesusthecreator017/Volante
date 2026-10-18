// Settings sheet: the Raspberry Pi server (optional plumbing most players set up once), heart-rate options, the intro, about.

import SwiftUI

struct SettingsView: View {
    @Environment(PiServer.self) private var pi
    @Environment(FitbitLink.self) private var fitbit
    @Environment(\.dismiss) private var dismiss
    @AppStorage("oso.onboarded") private var onboarded = false
    @AppStorage(FitbitLink.autoAttachKey) private var autoFitbit = true
    @AppStorage(HealthPrefs.ageKey) private var age = 0
    @AppStorage(HealthPrefs.maxHRKey) private var maxHR = 0.0
    @Environment(SessionStore.self) private var store
    @State private var checking = false

    var body: some View {
        @Bindable var pi = pi
        NavigationStack {
            Form {
                Section {
                    HStack {
                        Text("Status")
                        Spacer()
                        if pi.checking || checking { ProgressView().controlSize(.small) } else {
                            Text(piStatus).foregroundStyle(pi.reachable == true ? Theme.good : pi.reachable == false ? Theme.bad : .secondary)
                        }
                    }
                    TextField(PiServer.defaultURL, text: $pi.baseURL)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .onSubmit(check)
                    Button("Check connection", action: check).disabled(pi.checking || checking)
                    if let why = pi.problem, pi.reachable == false {
                        Label(why, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(Theme.bad)
                        if pi.needsTailscale {
                            Link(destination: URL(string: "https://apps.apple.com/app/tailscale/id1470499037")!) {
                                Label("Get Tailscale", systemImage: "arrow.down.app")
                            }
                        }
                    }
                } header: {
                    Text("Volante server")
                } footer: {
                    Text("Optional. A Raspberry Pi running the Volante server backs up your sessions, builds 3D replays and brings in Fitbit heart rate. The team Pi is reached over Tailscale: keep the Tailscale app connected on this iPhone.")
                }

                Section {
                    Stepper(value: $age, in: 0...100) { LabeledContent("Age", value: age == 0 ? "Not set" : "\(age)") }
                    Stepper(value: $maxHR, in: 0...230, step: 1) {
                        LabeledContent("Max heart rate", value: maxHR == 0 ? "Auto (\(Int(store.maxHR)) bpm)" : "\(Int(maxHR)) bpm")
                    }
                    if maxHR != 0 { Button("Use automatic max heart rate") { maxHR = 0 } }
                } header: {
                    Text("Heart rate")
                } footer: {
                    Text("Heart-rate zones are shares of your max heart rate: your own value if you set one, else 220 minus your age, else your highest recorded heart rate.")
                }

                Section {
                    Toggle("Add Fitbit heart rate automatically", isOn: $autoFitbit)
                } footer: {
                    Text("When Fitbit is connected, new sessions get your heart rate as soon as your tracker syncs.")
                }

                Section {
                    Button("Show the intro again") { onboarded = false; dismiss() }
                } footer: {
                    Text("Volante \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")")
                }
            }
            .themedList()
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done", systemImage: "checkmark") { dismiss() } } }
        }
    }

    private func check() {
        Task { checking = true; await pi.checkHealth(); await fitbit.refreshStatus(); checking = false }
    }
    private var piStatus: String {
        switch pi.reachable { case true?: "Connected"; case false?: "Unreachable"; case nil: "Not checked" }
    }
}
