// Sample data for previews and a DEBUG-only PNG renderer for the insights screens.

import OsoKit
import SwiftUI

enum InsightsSample {
    /// The bundled synthetic session (same file as the web app's sample), analysed.
    static func analysis(file: URL? = Bundle.main.url(forResource: "imu", withExtension: "bin")) -> SessionAnalysis? {
        guard let url = file,
              let d = try? Data(contentsOf: url), let cap = try? ImuFile.parse([UInt8](d)) else { return nil }
        return analyzeSession(cap)
    }

    /// A hit with a clear impact, preferably the fastest.
    static func swing(_ a: SessionAnalysis) -> Swing? {
        a.swings.filter { $0.impact }.max { $0.headSpeed < $1.headSpeed } ?? a.swings.first
    }

    static let meta = SessionMeta(id: "preview", name: "Sample session (synthetic)", createdAt: Date(timeIntervalSince1970: 1_760_700_000), source: .podUpload)

    /// A made-up history in which `meta` is the best session.
    static func bests(topSpeed: Double = 21.3) -> PersonalBests {
        return PersonalBests([
            .init(id: meta.id, isSample: false, mount: .racquet, maxSpeed: topSpeed, swings: 64, longestRally: 11),
            .init(id: "older", isSample: false, mount: .racquet, maxSpeed: topSpeed - 3, swings: 80, longestRally: 6),
        ])
    }
}

#if DEBUG
/// Renders the key views to PNG at the iPhone 16 Pro's 393 pt width. Does nothing without `-osoRenderInsights <dir>`.
struct DebugInsightsRender: ViewModifier {
    func body(content: Content) -> some View {
        content.task { await run() }
    }

    @MainActor private func run() async {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-osoRenderInsights"), let path = args[safe: i + 1] else { return }
        renderInsights(to: URL(fileURLWithPath: path, isDirectory: true), imu: nil)
        exit(0)
    }
}

/// Writes the PNGs; `imu` overrides the bundled sample (for the off-device harness).
@MainActor func renderInsights(to dir: URL, imu: URL?) {
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    guard let a = (imu.map { InsightsSample.analysis(file: $0) } ?? InsightsSample.analysis()), let s = InsightsSample.swing(a) else { return }
    let meta = InsightsSample.meta, bests = InsightsSample.bests(topSpeed: a.summary.maxSpeed ?? 21.3)

    func screen<V: View>(_ v: V, dark: Bool, type: DynamicTypeSize) -> some View {
        v.padding(.horizontal, 16).padding(.vertical, 12).frame(width: 393)
            .background(Theme.canvas)
            .environment(\.colorScheme, dark ? .dark : .light)
            .environment(\.dynamicTypeSize, type)
    }
    func write<V: View>(_ name: String, _ v: V, scale: CGFloat = 3) {
        let r = ImageRenderer(content: v)
        r.scale = scale
        if let d = r.uiImage?.pngData() { try? d.write(to: dir.appendingPathComponent(name + ".png")) }
    }
    for (name, dark, type) in [("swing-light", false, DynamicTypeSize.large), ("swing-dark", true, .large), ("swing-large-type", false, .accessibility2)] {
        write(name, screen(SwingDetailBody(analysis: a, swing: s, position: s.index - 1, count: a.swings.count), dark: dark, type: type), scale: 2)
    }
    if let low = a.swings.first(where: { $0.confidence < 0.8 }) {
        write("swing-low-confidence", screen(SwingDetailBody(analysis: a, swing: low, position: low.index - 1, count: a.swings.count), dark: false, type: .large), scale: 2)
    }
    write("header-pb", screen(SessionHeaderView(meta: meta, analysis: a, bests: bests), dark: false, type: .large))
    write("header-pb-dark", screen(SessionHeaderView(meta: meta, analysis: a, bests: bests), dark: true, type: .large))
    write("share-card", ShareCardView(analysis: a, date: meta.createdAt, personalBest: true), scale: 1)
    if let png = renderShareCard(a, date: meta.createdAt, personalBest: false) { try? png.write(to: dir.appendingPathComponent("share-card-export.png")) }
}
#endif
