// The Live Activity for a pod session: data model and the four layouts (Lock Screen, compact, minimal, expanded).
// Compiled into the OsoPhone app (controller + the DEBUG preview screen) and the OsoLive widget extension.
// Not in Shared/: ActivityKit does not exist for the watch target that folder is also built into.

import ActivityKit
import SwiftUI

struct LiveSessionAttributes: ActivityAttributes {
    enum Mode: String, Codable, Hashable { case streaming, recording }

    struct ContentState: Codable, Hashable {
        var swings: Int
        var hits: Int
        /// head speed of the last swing, m/s
        var lastSpeed: Double?
        var bestSpeed: Double
        /// session start, for the self-updating timer
        var start: Date
        /// false while the pod link is down ("reconnecting")
        var connected: Bool
        /// set when the session ends: the timer stops there while the final state stays on screen
        var ended: Date?
    }

    var podName: String
    var mode: Mode
}

// MARK: look (fixed colours: the island is always black, the Lock Screen card is always court green)

enum LiveStyle {
    static let courtTop = Color(red: 0x1E / 255, green: 0x7A / 255, blue: 0x54 / 255)
    static let courtBottom = Color(red: 0x0F / 255, green: 0x4A / 255, blue: 0x33 / 255)
    /// shuttle orange, on dark
    static let shuttle = Color(red: 1, green: 0x6A / 255, blue: 0x3D / 255)
    static let mint = Color(red: 0xD6 / 255, green: 0xFB / 255, blue: 0xE8 / 255)
    static let warn = Color(red: 1, green: 0xF1 / 255, blue: 0xC2 / 255)

    static func number(_ size: CGFloat) -> Font { .system(size: size, weight: .heavy, design: .rounded).monospacedDigit() }
}

extension LiveSessionAttributes.Mode {
    var label: String { self == .streaming ? "Live session" : "Recording" }
    var icon: String { self == .streaming ? "waveform.path.ecg" : "record.circle" }
}

extension LiveSessionAttributes.ContentState {
    var timerRange: ClosedRange<Date> { start...max(start, ended ?? .distantFuture) }
    var lastText: String { lastSpeed.map { String(format: "%.1f", $0) } ?? "–" }
    var bestText: String { bestSpeed > 0 ? String(format: "%.1f", bestSpeed) : "–" }
}

private struct Stat: View {
    let value: String
    var unit: String?
    let label: String
    var size: CGFloat = 30
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value).font(LiveStyle.number(size)).lineLimit(1).minimumScaleFactor(0.6)
                if let unit { Text(unit).font(.caption2.weight(.semibold)).foregroundStyle(.white.opacity(0.92)) }
            }
            Text(label).font(.caption2).foregroundStyle(.white.opacity(0.92)).lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct ElapsedTimer: View {
    let state: LiveSessionAttributes.ContentState
    var size: CGFloat = 20
    var body: some View {
        Text(timerInterval: state.timerRange, pauseTime: nil, countsDown: false, showsHours: false)
            .font(LiveStyle.number(size))
            .multilineTextAlignment(.trailing)
            .frame(maxWidth: size * 4.2, alignment: .trailing)
    }
}

private struct Reconnecting: View {
    var body: some View {
        Label("Reconnecting", systemImage: "wifi.exclamationmark").font(.caption.weight(.semibold)).foregroundStyle(LiveStyle.warn)
    }
}

// MARK: Lock Screen / banner

struct LiveLockScreenView: View {
    let attributes: LiveSessionAttributes
    let state: LiveSessionAttributes.ContentState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(attributes.podName, systemImage: "figure.badminton").font(.subheadline.weight(.semibold)).lineLimit(1)
                Spacer(minLength: 8)
                if state.connected {
                    Label(attributes.mode.label, systemImage: attributes.mode.icon).font(.caption.weight(.semibold)).foregroundStyle(LiveStyle.mint)
                } else {
                    Reconnecting()
                }
            }
            if attributes.mode == .recording {
                HStack(alignment: .bottom, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Recording on pod").font(.system(size: 22, weight: .heavy, design: .rounded)).lineLimit(1).minimumScaleFactor(0.7)
                        Text("Swings appear after upload").font(.caption2).foregroundStyle(.white.opacity(0.92))
                    }
                    Spacer(minLength: 0)
                    ElapsedTimer(state: state, size: 22)
                }
            } else {
                HStack(alignment: .bottom, spacing: 18) {
                    Stat(value: "\(state.swings)", label: state.hits > 0 ? "Swings (\(state.hits) hits)" : "Swings", size: 38)
                    Stat(value: state.lastText, unit: "m/s", label: "Last", size: 30)
                    Stat(value: state.bestText, unit: "m/s", label: "Best", size: 30)
                    Spacer(minLength: 0)
                    ElapsedTimer(state: state, size: 22)
                }
            }
        }
        .foregroundStyle(.white)
        .padding(16)
        .background(LinearGradient(colors: [LiveStyle.courtTop, LiveStyle.courtBottom], startPoint: .topLeading, endPoint: .bottomTrailing))
    }
}

// MARK: Dynamic Island

struct LiveCompactLeading: View {
    var body: some View { Image(systemName: "figure.badminton").foregroundStyle(LiveStyle.shuttle) }
}

struct LiveCompactTrailing: View {
    let attributes: LiveSessionAttributes
    let state: LiveSessionAttributes.ContentState
    var body: some View {
        if attributes.mode == .recording {
            Text(timerInterval: state.timerRange, pauseTime: nil, countsDown: false, showsHours: false)
                .font(LiveStyle.number(13)).lineLimit(1).minimumScaleFactor(0.7).multilineTextAlignment(.trailing).frame(maxWidth: 48, alignment: .trailing)
                .foregroundStyle(state.connected ? LiveStyle.shuttle : LiveStyle.warn)
        } else {
            Text("\(state.swings)").font(LiveStyle.number(15)).foregroundStyle(state.connected ? LiveStyle.shuttle : LiveStyle.warn)
        }
    }
}

struct LiveMinimal: View {
    let attributes: LiveSessionAttributes
    let state: LiveSessionAttributes.ContentState
    var body: some View {
        if attributes.mode == .recording {
            Image(systemName: "record.circle.fill").foregroundStyle(state.connected ? Color.red : LiveStyle.warn)
        } else {
            Text("\(state.swings)").font(LiveStyle.number(14)).foregroundStyle(state.connected ? LiveStyle.shuttle : LiveStyle.warn)
        }
    }
}

struct LiveExpandedLeading: View {
    let attributes: LiveSessionAttributes
    let state: LiveSessionAttributes.ContentState
    var body: some View {
        if attributes.mode == .recording {
            Label("Recording on pod", systemImage: "record.circle.fill").font(.subheadline.weight(.bold)).foregroundStyle(.white).lineLimit(1)
        } else {
            Stat(value: "\(state.swings)", label: "Swings", size: 34).foregroundStyle(.white)
        }
    }
}

struct LiveExpandedTrailing: View {
    let state: LiveSessionAttributes.ContentState
    var body: some View { ElapsedTimer(state: state, size: 26).foregroundStyle(.white) }
}

struct LiveExpandedBottom: View {
    let attributes: LiveSessionAttributes
    let state: LiveSessionAttributes.ContentState
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if attributes.mode == .recording {
                Text("\(attributes.podName) · swings appear after upload").font(.caption).foregroundStyle(.white.opacity(0.92)).lineLimit(1)
            } else {
                HStack(alignment: .bottom, spacing: 22) {
                    Stat(value: state.lastText, unit: "m/s", label: "Last", size: 26)
                    Stat(value: state.bestText, unit: "m/s", label: "Best", size: 26)
                    Stat(value: "\(state.hits)", label: "Hits", size: 26)
                    Spacer(minLength: 0)
                }
            }
            if state.connected {
                Label("\(attributes.mode.label) · \(attributes.podName)", systemImage: attributes.mode.icon)
                    .font(.caption.weight(.semibold)).foregroundStyle(LiveStyle.mint).lineLimit(1)
            } else {
                Reconnecting()
            }
        }
        .foregroundStyle(.white)
    }
}
