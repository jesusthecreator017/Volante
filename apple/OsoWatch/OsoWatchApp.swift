// Volante on Apple Watch: a badminton workout that records the wrist, counts swings and shows heart rate, styled like
// the iPhone app (the site's palette in its dark appearance, scoreboard numerals). Pages like Apple's Workout app:
// controls on the left, metrics in the middle. The workout session keeps the app on the wrist raise and in Always On.

import HealthKit
import SwiftUI
import WatchKit

/// The iPhone starts a badminton workout here (HKHealthStore.startWatchApp) when a live pod session starts.
final class WatchAppDelegate: NSObject, WKApplicationDelegate {
    @MainActor static var workout: WorkoutManager?
    func handle(_ workoutConfiguration: HKWorkoutConfiguration) {
        Task { @MainActor in
            guard let w = Self.workout, w.phase == .idle || w.phase == .sent else { return }
            await w.requestAuthorization()
            await w.start()
        }
    }
    /// The app was relaunched while its workout was running: take the session back.
    func handleActiveWorkoutRecovery() {
        Task { @MainActor in await Self.workout?.recover() }
    }
}

@main
struct OsoWatchApp: App {
    @WKApplicationDelegateAdaptor private var delegate: WatchAppDelegate
    @State private var workout: WorkoutManager = {
        let w = WorkoutManager()
        WatchAppDelegate.workout = w
        return w
    }()

    var body: some Scene {
        WindowGroup {
            RootView().environment(workout)
                .task {
                    await workout.requestAuthorization()
                    #if DEBUG
                    // -osoDemoWorkout: start a workout at launch (screenshots, and the simulator's demo motion source)
                    if ProcessInfo.processInfo.arguments.contains("-osoDemoWorkout") { await workout.start() }
                    #endif
                }
        }
    }
}

/// The iPhone app's palette (Theme.swift, dark appearance): pine surfaces, sage for good, shuttle orange for action.
enum WatchTheme {
    static let canvas = Color(hex: 0x142019)
    static let card = Color(hex: 0x1D2D24)
    static let courtTop = Color(hex: 0x3A5A40)
    static let courtBottom = Color(hex: 0x26392E)
    static let good = Color(hex: 0xA3B18A)
    static let hot = Color(hex: 0xFF6A33)
    static let heart = Color(hex: 0xFF7676)
    static let banner = LinearGradient(colors: [courtTop, courtBottom], startPoint: .topLeading, endPoint: .bottomTrailing)

    static func score(_ size: CGFloat) -> Font { .system(size: size, weight: .heavy, design: .rounded).monospacedDigit() }
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 255) / 255, green: Double((hex >> 8) & 255) / 255, blue: Double(hex & 255) / 255)
    }
}

struct RootView: View {
    @Environment(WorkoutManager.self) private var w

    var body: some View {
        Group {
            switch w.phase {
            case .idle, .sent: StartView()
            case .starting: ProgressView("Starting…").tint(WatchTheme.hot)
            case .running, .ending: SessionPager()
            }
        }
        .containerBackground(WatchTheme.canvas.gradient, for: .navigation)
    }
}

// MARK: start

struct StartView: View {
    @Environment(WorkoutManager.self) private var w

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 10) {
                    Button { Task { await w.start() } } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Image(systemName: "figure.badminton")
                                .font(.title2.weight(.semibold))
                                .foregroundStyle(WatchTheme.hot)
                                .frame(width: 40, height: 40)
                                .background(.black.opacity(0.25), in: Circle())
                            Text("Badminton").font(.headline)
                            Text("Swings, heart rate, and your pod").font(.caption2).foregroundStyle(.white.opacity(0.8))
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(WatchTheme.banner, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Start badminton")

                    if let r = w.lastResult {
                        HStack {
                            Image(systemName: w.phase == .sent ? "checkmark.circle.fill" : "clock")
                                .foregroundStyle(WatchTheme.good)
                            VStack(alignment: .leading, spacing: 0) {
                                Text("\(r.swings) swings · \(Duration.seconds(r.duration).formatted(.time(pattern: .minuteSecond)))")
                                    .font(.footnote.weight(.semibold))
                                Text(w.phase == .sent ? "Sent to iPhone" : "Last workout").font(.caption2).foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(10)
                        .background(WatchTheme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    }

                    if WKInterfaceDevice.current().wristLocation == .left {
                        Tip(icon: "hand.raised", text: "Swings count from the wrist that swings: wear the watch on your racquet arm.")
                    }
                    if let e = w.error { Tip(icon: "exclamationmark.triangle", text: e, color: WatchTheme.heart) }
                }
            }
            .navigationTitle("Volante")
        }
    }
}

struct Tip: View {
    let icon: String
    let text: String
    var color: Color = WatchTheme.good
    var body: some View {
        Label { Text(text).font(.caption2) } icon: { Image(systemName: icon).foregroundStyle(color) }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(WatchTheme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

// MARK: running

struct SessionPager: View {
    @Environment(\.isLuminanceReduced) private var dimmed
    @State private var page = 1

    var body: some View {
        TabView(selection: $page) {
            ControlsView().tag(0)
            MetricsView().tag(1)
        }
        .tabViewStyle(.page(indexDisplayMode: dimmed ? .never : .automatic))
        .onChange(of: dimmed) { _, d in if d { page = 1 } }  // Always On shows the numbers
    }
}

struct MetricsView: View {
    @Environment(WorkoutManager.self) private var w
    @Environment(\.isLuminanceReduced) private var dimmed

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 4) {
                Image(systemName: "figure.badminton")
                if w.mirrored { Image(systemName: "iphone").accessibilityLabel("On iPhone") }
                Spacer()
            }
            .font(.caption2.weight(.semibold))
            .foregroundStyle(WatchTheme.good)

            Text(timerInterval: w.startDate...Date.distantFuture, countsDown: false, showsHours: false)
                .font(WatchTheme.score(30))
                .foregroundStyle(dimmed ? WatchTheme.hot.opacity(0.7) : WatchTheme.hot)
                .accessibilityLabel("Elapsed")

            metric(value: "\(w.swings)", unit: "SWINGS", color: .white, size: 40)
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                metric(value: w.heartRate.map { String(format: "%.0f", $0) } ?? "--", unit: "BPM", color: WatchTheme.heart, size: 24,
                       icon: "heart.fill")
                metric(value: String(format: "%.0f", w.activeEnergy), unit: "KCAL", color: .white, size: 24)
            }
            if !dimmed {
                HStack(spacing: 12) {
                    small(w.hits, "hits")
                    if let s = w.bestSpeed { small(Int((s * 3.6).rounded()), "best km/h") }
                }
            }
            if w.heartRateMissing, !dimmed {
                Text("No heart rate: Settings > Health > Apps > Volante").font(.system(size: 11)).foregroundStyle(.secondary)
            } else if let e = w.error, !dimmed {
                Text(e).font(.system(size: 11)).foregroundStyle(WatchTheme.heart).lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 4)
        .opacity(dimmed ? 0.85 : 1)
    }

    private func metric(value: String, unit: String, color: Color, size: CGFloat, icon: String? = nil) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(value).font(WatchTheme.score(size)).foregroundStyle(color).contentTransition(.numericText())
            if let icon { Image(systemName: icon).font(.system(size: 11)).foregroundStyle(color) }
            Text(unit).font(.system(size: 11, weight: .semibold, design: .rounded)).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }

    private func small(_ v: Int, _ label: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text("\(v)").font(WatchTheme.score(15))
            Text(label).font(.system(size: 11)).foregroundStyle(.secondary)
        }
    }
}

struct ControlsView: View {
    @Environment(WorkoutManager.self) private var w

    var body: some View {
        VStack(spacing: 10) {
            Button { Task { await w.end() } } label: {
                Image(systemName: "xmark").font(.title2.weight(.bold)).frame(maxWidth: .infinity, minHeight: 44)
            }
            .tint(WatchTheme.heart)
            .disabled(w.phase == .ending)
            .accessibilityLabel("End workout")
            Text(w.phase == .ending ? "Saving…" : "End").font(.footnote)
            Text(w.mirrored ? "Ends on your iPhone too" : "Sends the session to your iPhone")
                .font(.caption2).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding(.horizontal)
    }
}
