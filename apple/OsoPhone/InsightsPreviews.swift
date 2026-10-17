// Previews for the session-insights screens, using the bundled synthetic session (see InsightsRender.swift).

import OsoKit
import SwiftUI

#Preview("Swing detail") {
    if let a = InsightsSample.analysis(), let s = InsightsSample.swing(a) {
        NavigationStack { SwingDetailView(analysis: a, start: s.index) }
    }
}

#Preview("Swing detail, dark, large type") {
    if let a = InsightsSample.analysis(), let s = InsightsSample.swing(a) {
        NavigationStack { SwingDetailView(analysis: a, start: s.index) }
            .preferredColorScheme(.dark).dynamicTypeSize(.accessibility2)
    }
}

#Preview("Header with personal best") {
    if let a = InsightsSample.analysis() {
        SessionHeaderView(meta: InsightsSample.meta, analysis: a, bests: InsightsSample.bests())
            .padding(16).background(Theme.canvas)
    }
}

#Preview("Share card") {
    if let a = InsightsSample.analysis() {
        ShareCardView(analysis: a, date: InsightsSample.meta.createdAt, personalBest: true)
            .scaleEffect(0.35, anchor: .topLeading).frame(width: 378, height: 473, alignment: .topLeading)
    }
}

