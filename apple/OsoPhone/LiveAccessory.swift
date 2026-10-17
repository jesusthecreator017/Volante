// The tab bar's bottom accessory while the pod is busy (like Music's now playing): live swings, recording or download progress.

import OsoKit
import SwiftUI

extension PodLink {
    /// A live stream, an on-pod recording or a download is running.
    var isBusy: Bool { streaming || uploading || status?.recording == true }
}

struct LiveAccessory: View {
    @Environment(PodLink.self) private var pod
    @Environment(Router.self) private var router
    @Environment(\.tabViewBottomAccessoryPlacement) private var placement

    var body: some View {
        Button { router.tab = .pod } label: {
            HStack(spacing: 10) {
                Image(systemName: icon)
                    .foregroundStyle(pod.status?.recording == true && !pod.streaming ? Theme.bad : Theme.good)
                    .symbolEffect(.pulse, options: .repeating)
                Text(title).font(.subheadline.weight(.semibold)).lineLimit(1)
                Spacer(minLength: 4)
                if placement != .inline, let detail { Text(detail).font(.subheadline.monospacedDigit()).foregroundStyle(.secondary) }
            }
            .padding(.horizontal, 16)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var icon: String {
        pod.streaming ? "waveform.path.ecg" : pod.uploading ? "arrow.down.circle.fill" : "record.circle"
    }
    private var title: String {
        if pod.streaming, let live = pod.live { return "\(live.swingCount) swings" }
        if pod.uploading { return "Downloading session" }
        return "Recording on the pod"
    }
    private var detail: String? {
        if pod.streaming { return pod.liveSwings.last.map { String(format: "%.1f m/s", $0.headSpeed) } }
        if pod.uploading, pod.uploadExpected > 0 { return "\(100 * pod.uploadReceived / pod.uploadExpected)%" }
        return nil
    }
}
