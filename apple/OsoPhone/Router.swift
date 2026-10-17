// Selected tab and the Sessions navigation path.

import SwiftUI

/// Selected tab and the Sessions navigation path, so other screens (and the DEBUG launch arguments) can drive them.
@MainActor
@Observable
final class Router {
    enum Tab: String { case pod, sessions, connections }
    var tab: Tab = .pod
    var path: [SessionMeta] = []

    /// Imports the bundled sample if needed and opens it in Sessions.
    func openSample(_ store: SessionStore) {
        if !store.sessions.contains(where: { $0.source == .sample }) { try? store.importSample() }
        if let m = store.sessions.first(where: { $0.source == .sample }) {
            tab = .sessions
            path = [m]
        }
    }
}
