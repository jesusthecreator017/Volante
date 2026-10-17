// oso for iPhone: racquet pod over Bluetooth, Apple Watch sessions, Fitbit heart rate. Analysis in OsoKit.

import OsoKit
import SwiftUI

@main
struct OsoPhoneApp: App {
    @State private var store = SessionStore()
    @State private var pod = PodLink()
    @State private var watch = WatchReceiver()
    @State private var pi: PiServer
    @State private var fitbit: FitbitLink

    init() {
        let pi = PiServer()
        _pi = State(initialValue: pi)
        _fitbit = State(initialValue: FitbitLink(pi: pi))
    }

    var body: some Scene {
        WindowGroup {
            TabView {
                PodView().tabItem { Label("Pod", systemImage: "sensor.tag.radiowaves.forward") }
                SessionsView().tabItem { Label("Sessions", systemImage: "list.bullet.rectangle") }
                WearablesView().tabItem { Label("Connections", systemImage: "antenna.radiowaves.left.and.right") }
            }
            .environment(store)
            .environment(pod)
            .environment(watch)
            .environment(fitbit)
            .environment(pi)
            .onAppear(perform: wire)
        }
    }

    private func wire() {
        pod.onSession = { imu, meta in
            do { try store.save(imu: imu, meta: meta) } catch { pod.lastError = "save failed: \(error)" }
        }
        watch.onSession = { url, meta in try? store.importFile(url, meta: meta) }
    }
}
