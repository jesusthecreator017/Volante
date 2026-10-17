// Selected tab and the Sessions navigation path.

import SwiftUI

/// Selected tab and the Sessions navigation path, so other screens (and the DEBUG launch arguments) can drive them.
@MainActor
@Observable
final class Router {
    enum Tab: String { case pod, sessions, connections }
    var tab: Tab = .pod
    var path: [SessionMeta] = []
}
