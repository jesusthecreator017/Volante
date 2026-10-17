// oso on Apple Watch: a badminton workout that records the wrist and counts swings.

import SwiftUI

@main
struct OsoWatchApp: App {
    @State private var workout = WorkoutManager()

    var body: some Scene {
        WindowGroup {
            WorkoutView().environment(workout)
                .task { await workout.requestAuthorization() }
        }
    }
}

struct WorkoutView: View {
    @Environment(WorkoutManager.self) private var w

    var body: some View {
        ScrollView {
            VStack(spacing: 8) {
                switch w.phase {
                case .idle, .sent:
                    if w.phase == .sent { Text("Sent to iPhone ✓").font(.footnote).foregroundStyle(.green) }
                    Button { Task { await w.start() } } label: {
                        Label("Badminton", systemImage: "figure.badminton").frame(maxWidth: .infinity)
                    }
                    .tint(.orange)
                    Text("Records wrist motion and heart rate. Hold still a moment, then play.")
                        .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                case .starting:
                    ProgressView("Starting…")
                case .running, .ending:
                    Text(Duration.seconds(w.elapsed).formatted(.time(pattern: .minuteSecond)))
                        .font(.system(.title2, design: .rounded).monospacedDigit())
                    HStack {
                        stat("\(w.swings)", "swings")
                        stat("\(w.hits)", "hits")
                    }
                    HStack {
                        stat(w.heartRate.map { String(format: "%.0f", $0) } ?? "–", "bpm")
                        stat(String(format: "%.0f", w.activeEnergy), "kcal")
                    }
                    if let d = w.lastSwingDps { Text(String(format: "last swing %.0f°/s", d)).font(.footnote) }
                    Button("End", role: .destructive) { Task { await w.end() } }
                        .disabled(w.phase == .ending)
                }
                if let e = w.error { Text(e).font(.footnote).foregroundStyle(.red) }
            }
            .padding(.horizontal)
        }
    }

    private func stat(_ value: String, _ label: String) -> some View {
        VStack {
            Text(value).font(.system(.title3, design: .rounded).monospacedDigit())
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}
