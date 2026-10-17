// DEBUG-only: -osoLivePreview draws the Live Activity layouts inside the app (so they can be screenshotted without
// capturing the Lock Screen). The island shapes are mock-ups of the real thing; the contents are the real views.

#if DEBUG
import SwiftUI

struct LivePreviewView: View {
    private typealias State = LiveSessionAttributes.ContentState
    private let streaming = LiveSessionAttributes(podName: "oso pod", mode: .streaming)
    private let recording = LiveSessionAttributes(podName: "oso pod", mode: .recording)
    private let state = State(swings: 42, hits: 31, lastSpeed: 47.3, bestSpeed: 58.1, start: Date().addingTimeInterval(-754), connected: true)
    private var lost: State { var s = state; s.connected = false; return s }
    private var variants: [(LiveSessionAttributes, State)] { [(streaming, state), (streaming, lost), (recording, state), (recording, lost)] }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                label("Lock Screen / banner: live, reconnecting, recording, recording reconnecting")
                ForEach(variants.indices, id: \.self) { i in
                    LiveLockScreenView(attributes: variants[i].0, state: variants[i].1).clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                }
                label("Compact")
                ForEach(variants.indices, id: \.self) { i in compact(variants[i].0, variants[i].1) }
                label("Minimal")
                HStack(spacing: 12) { ForEach(variants.indices, id: \.self) { i in minimal(variants[i].0, variants[i].1) } }
                label("Expanded")
                ForEach(variants.indices, id: \.self) { i in expanded(variants[i].0, variants[i].1) }
            }
            .padding(16)
        }
        .background(Color.black.ignoresSafeArea())
        .preferredColorScheme(.dark)
    }

    private func label(_ t: String) -> some View { Text(t).font(.caption.weight(.semibold)).foregroundStyle(.gray) }

    private func compact(_ a: LiveSessionAttributes, _ s: State) -> some View {
        HStack(spacing: 70) { LiveCompactLeading(); LiveCompactTrailing(attributes: a, state: s) }
            .padding(.horizontal, 16).frame(height: 37)
            .background(Color(white: 0.1), in: Capsule())
    }

    private func minimal(_ a: LiveSessionAttributes, _ s: State) -> some View {
        LiveMinimal(attributes: a, state: s).frame(width: 37, height: 37).background(Color(white: 0.1), in: Circle())
    }

    private func expanded(_ a: LiveSessionAttributes, _ s: State) -> some View {
        VStack(spacing: 8) {
            HStack(alignment: .top) { LiveExpandedLeading(attributes: a, state: s); Spacer(); LiveExpandedTrailing(state: s) }
            LiveExpandedBottom(attributes: a, state: s).frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(18)
        .background(Color(white: 0.1), in: RoundedRectangle(cornerRadius: 44, style: .continuous))
    }
}

#Preview { LivePreviewView() }
#endif
