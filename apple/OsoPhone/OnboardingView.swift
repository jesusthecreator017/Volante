// First-launch intro: what oso is, why it wants Bluetooth (asked at the moment it makes sense), the optional Pi, and
// the two ways to start. Shown once (`oso.onboarded`), skippable on every page, replayable from Connections.

import SwiftUI

struct OnboardingView: View {
    /// Called when the flow ends, however it ends; the app marks the intro as seen.
    var onFinish: () -> Void

    @Environment(PodLink.self) private var pod
    @Environment(SessionStore.self) private var store
    @Environment(Router.self) private var router
    @Environment(\.openURL) private var openURL
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var page = 0
    @State private var forward = true

    init(firstPage: Int = 0, onFinish: @escaping () -> Void) {
        self.onFinish = onFinish
        _page = State(initialValue: firstPage)
    }

    private enum Page: Int, CaseIterable { case welcome, bluetooth, network, start }
    private var current: Page { Page(rawValue: page) ?? .welcome }

    var body: some View {
        VStack(spacing: 0) {
            topBar
            // Not a paged TabView: Reduce Motion gets a plain cross-fade, and the page has one clear owner of its buttons
            content(current)
                .id(page)
                .transition(reduceMotion ? .opacity : .asymmetric(
                    insertion: .move(edge: forward ? .trailing : .leading), removal: .move(edge: forward ? .leading : .trailing)))
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .clipped()
                .simultaneousGesture(DragGesture(minimumDistance: 40).onEnded { v in
                    guard abs(v.translation.width) > 2 * abs(v.translation.height) else { return }
                    go(page + (v.translation.width < 0 ? 1 : -1))
                })
            actions
        }
        .background(Theme.canvas.ignoresSafeArea())
        .sensoryFeedback(.selection, trigger: page)
    }

    // MARK: chrome

    private var topBar: some View {
        HStack {
            dots
            Spacer(minLength: 8)
            Button("Skip") { onFinish() }
                .font(.body.weight(.semibold))
                .foregroundStyle(Theme.courtInk)
                .frame(minWidth: 44, minHeight: 44)
        }
        .padding(.horizontal, 20)
        .padding(.top, 4)
    }

    private var dots: some View {
        HStack(spacing: 6) {
            ForEach(Page.allCases, id: \.rawValue) { p in
                Capsule()
                    .fill(p == current ? Theme.courtInk : Theme.courtInk.opacity(0.28))
                    .frame(width: p == current ? 22 : 8, height: 8)
            }
        }
        .animation(reduceMotion ? nil : .snappy, value: page)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Page \(page + 1) of \(Page.allCases.count)")
        .accessibilityValue(title(current))
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: go(page + 1)
            case .decrement: go(page - 1)
            @unknown default: break
            }
        }
    }

    private func go(_ to: Int) {
        guard (0..<Page.allCases.count).contains(to) else { return }
        forward = to > page
        withAnimation(reduceMotion ? .easeInOut(duration: 0.2) : .smooth(duration: 0.35)) { page = to }
    }

    private func title(_ p: Page) -> String {
        switch p {
        case .welcome: "Welcome"
        case .bluetooth: "Bluetooth"
        case .network: "Your Pi"
        case .start: "Start"
        }
    }

    // MARK: pages

    @ViewBuilder private func content(_ p: Page) -> some View {
        ScrollView {
            VStack(spacing: 16) {
                switch p {
                case .welcome: welcome
                case .bluetooth: bluetooth
                case .network: network
                case .start: start
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
        }
        .scrollBounceBehavior(.basedOnSize)
    }

    private func hero(_ symbol: String, _ headline: String, _ text: String) -> some View {
        Banner {
            Image(systemName: symbol).font(.system(size: 54, weight: .semibold)).accessibilityHidden(true)
                .symbolEffect(.bounce, options: .nonRepeating, value: reduceMotion ? 0 : page)
            Text(headline).font(.system(.title, design: .rounded, weight: .heavy)).accessibilityAddTraits(.isHeader)
            Text(text).font(.subheadline).foregroundStyle(.white.opacity(0.9))
        }
    }

    private func point(_ symbol: String, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Image(systemName: symbol).foregroundStyle(Theme.courtInk).frame(width: 26).accessibilityHidden(true)
            Text(text).font(.subheadline).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder private var welcome: some View {
        hero("figure.badminton", "Your swing, measured.",
             "The Volante pod clips to your racquet and counts every swing over Bluetooth, with head speed, stroke type and impact timing.")
        Card {
            point("speedometer", "Head speed on every swing")
            point("tag", "Smash, clear, drop, drive, lift or net")
            point("waveform.path.ecg", "Whether you hit the shuttle, and when")
        }
    }

    @ViewBuilder private var bluetooth: some View {
        hero("dot.radiowaves.left.and.right", "Find your pod",
             "Volante uses Bluetooth to find the pod on your racquet and stream its swings. It is not used for anything else.")
        Card("Bluetooth", systemImage: "bolt.horizontal.circle") {
            switch pod.access {
            case .notDetermined:
                Text("iOS will ask once. Tap Allow Bluetooth, then choose Allow.").font(.subheadline)
            case .ready:
                StatusChip(text: "Bluetooth is on", systemImage: "checkmark.circle.fill", color: Theme.good)
                Text("Volante can find your pod.").font(.subheadline)
            case .denied:
                StatusChip(text: "Bluetooth is blocked for Volante", systemImage: "xmark.circle.fill", color: Theme.bad)
                Text("Turn it on in Settings, under Volante, to find your pod. You can still try the sample session.").font(.subheadline)
            case .poweredOff:
                StatusChip(text: "Bluetooth is off", systemImage: "exclamationmark.circle.fill", color: Theme.bad)
                Text("Open Control Center and tap Bluetooth. Volante finds your pod as soon as it is on.").font(.subheadline)
            case .unsupported:
                StatusChip(text: "Not available here", systemImage: "xmark.circle", color: .secondary)
                Text("This device can't use Bluetooth, so the pod won't connect. The sample session still works.").font(.subheadline)
            case .checking:
                HStack(spacing: 10) { ProgressView(); Text("Checking Bluetooth…").font(.subheadline) }
            }
        }
    }

    @ViewBuilder private var network: some View {
        hero("server.rack", "Your Raspberry Pi",
             "Optional. If you run the Volante server on a Pi, it stores sessions and brings in Fitbit heart rate over your Wi-Fi.")
        Card("Local network", systemImage: "wifi") {
            point("iphone.gen3", "The first time Volante looks for the Pi, iOS asks to find devices on your local network.")
            point("checkmark.circle", "Only the Pi on your Wi-Fi is contacted. The pod and Apple Watch need nothing else.")
            point("clock", "No Pi? Set up later; you can add it any time in Connections.")
        }
    }

    @ViewBuilder private var start: some View {
        hero("play.circle.fill", "Ready when you are",
             "Look around with a real recorded session, or scan for your pod.")
        Card {
            point("testtube.2", "The sample session is a real swing recording: speed, strokes and impacts.")
            point("sensor.tag.radiowaves.forward", "Wake your pod and tap Scan; it advertises for 2 s every 30 s.")
        }
    }

    // MARK: actions

    @ViewBuilder private var actions: some View {
        VStack(spacing: 10) {
            switch current {
            case .welcome:
                Button("Get started") { go(1) }.buttonStyle(.primary)
            case .bluetooth:
                switch pod.access {
                case .notDetermined:
                    Button { pod.enableBluetooth() } label: { Label("Allow Bluetooth", systemImage: "bolt.horizontal.circle") }.buttonStyle(.primary)
                    Button("Not now") { go(2) }.buttonStyle(.secondary)
                case .denied:
                    Button { openSettings() } label: { Label("Open Settings", systemImage: "gearshape") }.buttonStyle(.primary)
                    Button("Continue") { go(2) }.buttonStyle(.secondary)
                default:
                    Button("Continue") { go(2) }.buttonStyle(.primary)
                }
            case .network:
                Button { finish { router.tab = .connections } } label: { Label("Check my Pi", systemImage: "arrow.clockwise") }.buttonStyle(.primary)
                Button("Set up later") { go(3) }.buttonStyle(.secondary)
            case .start:
                Button { finish { router.openSample(store) } } label: { Label("Try the sample session", systemImage: "play.circle") }.buttonStyle(.primary)
                Button {
                    finish {
                        router.tab = .pod
                        if pod.access == .ready { pod.scan() }
                    }
                } label: { Label("Scan for my pod", systemImage: "dot.radiowaves.left.and.right") }.buttonStyle(.secondary)
            }
        }
        .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 12)
    }

    private func finish(_ then: () -> Void) {
        then()
        onFinish()
    }

    private func openSettings() {
        if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
    }
}
