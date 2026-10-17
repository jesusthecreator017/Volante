// DEBUG-only launch-argument hooks (see D6 of the design spec).

import SwiftUI

/// DEBUG-only launch arguments for deterministic screenshots (nothing here is compiled into Release):
///   -osoTab pod|sessions|connections   select a tab
///   -osoSample                         import the sample session if missing and open its detail
///   -osoResetSample                    delete saved sample sessions first
///   -osoHeartRate                      with -osoSample: attach synthetic heart rate (not saved)
///   -osoDark / -osoLargeType           force dark appearance / accessibility Dynamic Type
///   -osoLiveDemo                       start a Live Activity with a simulated 60 s session (no pod needed)
///   -osoLivePreview                    show the Live Activity layouts in the app instead of the tabs
///   -osoBottom                         start Screens scrolled to the bottom
struct DebugOverrides: ViewModifier {
    func body(content: Content) -> some View {
        #if DEBUG
        let args = ProcessInfo.processInfo.arguments
        content
            .preferredColorScheme(args.contains("-osoDark") ? .dark : nil)
            .modifier(LargeTypeOverride(on: args.contains("-osoLargeType")))
        #else
        content
        #endif
    }
}

#if DEBUG
private struct LargeTypeOverride: ViewModifier {
    let on: Bool
    func body(content: Content) -> some View {
        if on { content.dynamicTypeSize(.accessibility2) } else { content }
    }
}
#endif

/// -osoBottom: real scrolling (so the navigation bar's scroll-edge effect shows), starting at the bottom.
struct DebugScroll: ViewModifier {
    func body(content: Content) -> some View {
        #if DEBUG
        content.defaultScrollAnchor(ProcessInfo.processInfo.arguments.contains("-osoBottom") ? .bottom : .top)
        #else
        content
        #endif
    }
}
