// Volante for iPhone: racquet pod over Bluetooth, Apple Watch sessions, Fitbit heart rate. Analysis in OsoKit.

import OsoKit
import SwiftUI

@main
struct OsoPhoneApp: App {
    @State private var store: SessionStore
    @State private var pod: PodLink
    @State private var watch: WatchReceiver
    @State private var linker: SessionLinker
    @State private var pi: PiServer
    @State private var fitbit: FitbitLink
    @State private var router = Router()
    @State private var arena = ArenaClient()
    @AppStorage("oso.onboarded") private var onboarded = false
    /// DEBUG: -osoOnboarding shows the intro even when it was seen, until it ends.
    @State private var forceIntro = DebugOverrides.forcesIntro
    @Environment(\.scenePhase) private var scenePhase

    init() {
        let store = SessionStore(), pod = PodLink(), watch = WatchReceiver()
        _store = State(initialValue: store)
        _pod = State(initialValue: pod)
        _watch = State(initialValue: watch)
        // here, not in onAppear: a watch workout can launch the app in the background with no view on screen
        _linker = State(initialValue: SessionLinker(pod: pod, watch: watch, store: store))
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
            // inside the .environment calls, so the sheet (its own hierarchy) inherits them
            .sheet(isPresented: Bindable(router).showSettings) { SettingsView() }
            .environment(router)
            .environment(arena)
            .environment(store)
            .environment(pod)
            .environment(watch)
            .environment(fitbit)
            .environment(pi)
            .onAppear(perform: wire)
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { pod.refreshAuthorization(); syncHeartRate() }
            }
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
            Tab("Summary", systemImage: "chart.line.uptrend.xyaxis", value: Router.Tab.summary) { SummaryView() }
            Tab("Play", systemImage: "figure.badminton", value: Router.Tab.pod) { PodView() }
            Tab("Sessions", systemImage: "list.bullet.rectangle.portrait", value: Router.Tab.sessions) { SessionsView() }
            Tab("Arena", systemImage: "trophy", value: Router.Tab.arena) { ArenaView() }
            Tab("Devices", systemImage: "sensor.tag.radiowaves.forward", value: Router.Tab.connections) { WearablesView() }
        }
        .tabBarMinimizeBehavior(.onScrollDown)
        .tabViewBottomAccessory(isEnabled: pod.isBusy) { LiveAccessory() }
    }

    private func wire() {
        pod.liveActivity.endStale()
        pod.onSession = { imu, meta in
            do {
                let saved = try store.save(imu: imu, meta: meta)
                syncHeartRate()
                // the post-session Summary opens by itself (live session ended or a recording downloaded)
                router.tab = .sessions
                router.path = [saved]
            } catch { pod.lastError = "save failed: \(error)" }
        }
        syncHeartRate()
        #if DEBUG
        applyLaunchArguments()
        #endif
    }

    /// Fitbit heart rate for recent sessions that lack it (no-op unless Fitbit is connected through the Pi).
    private func syncHeartRate() {
        Task { if fitbit.connected == nil { await fitbit.refreshStatus() }; await fitbit.backfill(store) }
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

/// One session across the pod and the watch (Settings > Apple Watch > Start and stop together). Driven by callbacks on
/// the models, not view changes, so it also works when a watch workout launched the app in the background. Each side
/// only acts when the other is not already there, so nothing loops.
@MainActor
final class SessionLinker {
    private let pod: PodLink
    private let watch: WatchReceiver
    private let store: SessionStore
    private var linked: Bool { UserDefaults.standard.object(forKey: "volante.watch.linked") as? Bool ?? true }
    private var expiry: Task<Void, Never>?

    init(pod: PodLink, watch: WatchReceiver, store: SessionStore) {
        self.pod = pod
        self.watch = watch
        self.store = store
        watch.onSession = { [store] url, meta in try? store.importFile(url, meta: meta) }
        watch.onWorkoutChange = { [weak self] activeChanged in
            guard let self else { return }
            self.mirrorWatch()
            if activeChanged { self.linkWatchToPod(watch.workoutActive) }
        }
        pod.onStreamingChange = { [weak self] in self?.linkPodToWatch($0) }
        // a watch that went quiet (app killed, out of range) ends its part of the session
        expiry = Task { [weak watch] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(10))
                watch?.expireIfSilent()
            }
        }
    }

    /// A live pod session starts the watch workout; ending it ends the workout, whichever side started.
    private func linkPodToWatch(_ streaming: Bool) {
        guard linked else { return }
        if streaming {
            if !watch.workoutActive { Task { await watch.startWorkout() } }
        } else {
            watch.endWorkout()
        }
    }

    /// Badminton started on the watch starts a live pod session when the pod is connected and idle; ending the workout on
    /// the watch ends the pod session.
    private func linkWatchToPod(_ active: Bool) {
        guard linked else { return }
        if active, pod.state == .connected, !pod.streaming, !pod.uploading, pod.status?.sleeping == false {
            pod.startStream(settings: store.settings)
        } else if !active, pod.streaming {
            pod.stopStream(save: true)
        }
    }

    /// The watch's live numbers onto the shared Live Activity.
    private func mirrorWatch() {
        pod.watchWorkout = watch.workoutActive ? (swings: watch.liveSwings ?? 0, heartRate: watch.liveHeartRate, start: watch.workoutStart) : nil
    }
}
