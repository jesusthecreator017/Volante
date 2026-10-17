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
    @State private var router = Router()
    @AppStorage("oso.onboarded") private var onboarded = false
    /// DEBUG: -osoOnboarding shows the intro even when it was seen, until it ends.
    @State private var forceIntro = DebugOverrides.forcesIntro
    @Environment(\.scenePhase) private var scenePhase

    init() {
        let pi = PiServer()
        _pi = State(initialValue: pi)
        _fitbit = State(initialValue: FitbitLink(pi: pi))
    }

    var body: some Scene {
        WindowGroup {
            root
            .fullScreenCover(isPresented: Binding(
                get: { !onboarded && !DebugOverrides.skipsIntro || forceIntro },
                set: { if !$0 { onboarded = true; forceIntro = false } }
            )) {
                OnboardingView { onboarded = true; forceIntro = false }
                    .modifier(DebugOverrides())
            }
            .modifier(DebugOverrides())
            .environment(router)
            .environment(store)
            .environment(pod)
            .environment(watch)
            .environment(fitbit)
            .environment(pi)
            .onAppear(perform: wire)
            .onChange(of: scenePhase) { _, phase in if phase == .active { pod.refreshAuthorization() } }
        }
    }

    @ViewBuilder private var root: some View {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-osoLivePreview") { LivePreviewView() } else { tabs }
        #else
        tabs
        #endif
    }

    private var tabs: some View {
        TabView(selection: Bindable(router).tab) {
            PodView().tabItem { Label("Pod", systemImage: "sensor.tag.radiowaves.forward") }.tag(Router.Tab.pod)
            SessionsView().tabItem { Label("Sessions", systemImage: "list.bullet.rectangle") }.tag(Router.Tab.sessions)
            WearablesView().tabItem { Label("Connections", systemImage: "antenna.radiowaves.left.and.right") }.tag(Router.Tab.connections)
        }
    }

    private func wire() {
        pod.liveActivity.endStale()
        pod.onSession = { imu, meta in
            do { try store.save(imu: imu, meta: meta) } catch { pod.lastError = "save failed: \(error)" }
        }
        watch.onSession = { url, meta in try? store.importFile(url, meta: meta) }
        #if DEBUG
        applyLaunchArguments()
        #endif
    }

    #if DEBUG
    /// See DebugOverrides in DebugOverrides.swift.
    private func applyLaunchArguments() {
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "-osoBluetooth"), i + 1 < args.count {
            pod.accessOverride = ["notDetermined": .notDetermined, "denied": .denied, "off": .poweredOff, "ready": .ready][args[i + 1]]
        }
        if args.contains("-osoLiveDemo") { pod.liveActivity.runDemo() }
        if let i = args.firstIndex(of: "-osoTab"), i + 1 < args.count, let tab = Router.Tab(rawValue: args[i + 1]) { router.tab = tab }
        if args.contains("-osoResetSample") { for m in store.sessions where m.source == .sample { store.delete(m.id) } }
        if args.contains("-osoSample") {
            if !store.sessions.contains(where: { $0.source == .sample }) { try? store.importSample() }
            if var m = store.sessions.first(where: { $0.source == .sample }) {
                if args.contains("-osoHeartRate"), m.heartRate.isEmpty, let a = store.analysis(m) {
                    // synthetic heart rate (in memory only) so the chart can be screenshotted
                    let wall = Date().timeIntervalSince1970
                    m.anchor = ClockAnchor(deviceT: a.t0, wall: wall)
                    m.heartRate = stride(from: -3.0, through: a.t1 - a.t0 + 3, by: 1).map { HeartRateSample(t: wall + $0, bpm: 112 + 1.8 * $0 + 4 * sin($0)) }
                    m.heartRateSource = "Synthetic (debug)"
                }
                router.tab = .sessions
                router.path = [m]
            }
        }
    }
    #endif
}
