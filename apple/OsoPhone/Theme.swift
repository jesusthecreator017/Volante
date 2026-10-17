// Volante look: system surfaces, Liquid Glass controls, scoreboard numbers. Colors are tokens here so the palette can be swapped in one place.

import OsoKit
import SwiftUI

enum Theme {
    // The site's palette (site/src/style.css): paper, sage, fern, moss, pine; deep green with white accents in dark mode.
    /// Page background (site --surface-2 / --bg) and content cards (--surface).
    static let canvas = Color(light: 0xECEAE4, dark: 0x142019)
    static let card = Color(light: 0xFFFFFF, dark: 0x1D2D24)
    static let radius: CGFloat = 26
    /// Header banners (white text on top, in both appearances): pine blocks (--court).
    static let courtTop = Color(light: 0x3A5A40, dark: 0x26392E)
    static let courtBottom = Color(light: 0x344E41, dark: 0x1D2D24)
    /// Text and icons in brand ink on a card (--text / --accent).
    static let courtInk = Color(light: 0x344E41, dark: 0xFFFFFF)
    /// Fill of the main buttons (--btn) and the text on it (--accent-text).
    static let action = Color(light: 0x3A5A40, dark: 0xFFFFFF)
    static let onAction = Color(light: 0xFFFFFF, dark: 0x142019)
    /// Status: fern / sage keep "good" readable as green (the site uses white in dark mode).
    static let good = Color(light: 0x588157, dark: 0xA3B18A)
    static let bad = Color(light: 0xB02A2A, dark: 0xFF7676)
    /// Destructive button fill under white text (>= 4.5:1 in both appearances).
    static let badFill = Color(light: 0xB02A2A, dark: 0xB3261E)
    /// Shuttle orange (--hot): graphics only.
    static let hot = Color(light: 0xFF5A1F, dark: 0xFF6A33)

    static let banner = LinearGradient(colors: [courtTop, courtBottom], startPoint: .topLeading, endPoint: .bottomTrailing)

    static func stroke(_ s: StrokeType) -> Color {
        switch s {
        case .smash: Theme.hot
        case .clear: Color(light: 0x2F6FDE, dark: 0x6AA2FF)
        case .drop: Color(light: 0x14A39A, dark: 0x3FD6CB)
        case .drive: Color(light: 0xD79A00, dark: 0xFFC83D)
        case .lift: Color(light: 0x8A4FD6, dark: 0xB78CFF)
        case .net: Color(light: 0xD6407E, dark: 0xFF7FB0)
        case .noContact, .unclassified: Color(light: 0x7A8680, dark: 0x9AA8A1)
        }
    }
}

extension Color {
    init(light: UInt32, dark: UInt32) {
        self.init(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(hex: dark) : UIColor(hex: light) })
    }
}

private extension UIColor {
    convenience init(hex: UInt32) {
        self.init(red: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255, blue: CGFloat(hex & 255) / 255, alpha: 1)
    }
}

extension Font {
    /// Scoreboard numerals: heavy rounded, scales with Dynamic Type.
    static func score(_ style: Font.TextStyle = .largeTitle) -> Font {
        .system(style, design: .rounded, weight: .heavy).monospacedDigit()
    }
}

/// A rounded card with an optional icon + title.
struct Card<Content: View>: View {
    var title: String?
    var systemImage: String?
    @ViewBuilder var content: Content

    init(_ title: String? = nil, systemImage: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.systemImage = systemImage
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let title {
                HStack(spacing: 8) {
                    if let systemImage { Image(systemName: systemImage).foregroundStyle(.tint) }
                    Text(title).font(.headline)
                }
                .accessibilityAddTraits(.isHeader)
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }
}

/// Big number with a unit and a caption.
struct BigStat: View {
    let value: String
    var unit: String?
    let label: String
    var onBanner = false
    var style: Font.TextStyle = .largeTitle

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value).font(.score(style)).lineLimit(1).minimumScaleFactor(0.5)
                if let unit { Text(unit).font(.footnote.weight(.semibold)).foregroundStyle(onBanner ? .white.opacity(0.75) : .secondary) }
            }
            Text(label).font(.caption).foregroundStyle(onBanner ? .white.opacity(0.75) : .secondary).lineLimit(2)
        }
        .foregroundStyle(onBanner ? Color.white : Color.primary)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// Adaptive grid: 2 to 4 columns on a phone, 1 at the largest text sizes.
struct StatGrid<Content: View>: View {
    var minimum: CGFloat = 120
    @ViewBuilder var content: Content
    @Environment(\.dynamicTypeSize) private var type
    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: type.isAccessibilitySize ? 280 : minimum), spacing: 14, alignment: .topLeading)], alignment: .leading, spacing: 14) { content }
    }
}

struct StatusChip: View {
    let text: String
    var systemImage: String?
    var color: Color = .secondary

    var body: some View {
        HStack(spacing: 4) {
            if let systemImage { Image(systemName: systemImage).imageScale(.small) }
            Text(text).lineLimit(1)
        }
        .font(.caption.weight(.semibold))
        .padding(.horizontal, 10).padding(.vertical, 5)
        .foregroundStyle(color)
        .background(color.opacity(0.14), in: Capsule())
    }
}

/// Green banner at the top of a screen.
struct Banner<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 14) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
            .background(Theme.banner, in: RoundedRectangle(cornerRadius: Theme.radius + 4, style: .continuous))
            .foregroundStyle(.white)
    }
}

/// Filled Liquid Glass capsule, tinted with the action color (the main action of a screen).
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    var destructive = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity, minHeight: 30)
            .padding(.vertical, 12).padding(.horizontal, 16)
            .foregroundStyle((destructive ? Color.white : Theme.onAction).opacity(enabled ? 1 : 0.6))
            .glassEffect(.regular.tint((destructive ? Theme.badFill : Theme.action).opacity(enabled ? 1 : 0.35)).interactive(), in: .capsule)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.snappy(duration: 0.18), value: configuration.isPressed)
    }
}

/// Clear Liquid Glass capsule for the other actions.
struct SecondaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    var destructive = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity, minHeight: 30)
            .padding(.vertical, 12).padding(.horizontal, 16)
            .foregroundStyle((destructive ? Theme.bad : Color.primary).opacity(enabled ? 1 : 0.4))
            .glassEffect(.regular.interactive(), in: .capsule)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.snappy(duration: 0.18), value: configuration.isPressed)
    }
}

extension ButtonStyle where Self == PrimaryButtonStyle {
    static var primary: PrimaryButtonStyle { .init() }
    static var primaryDestructive: PrimaryButtonStyle { .init(destructive: true) }
}
extension ButtonStyle where Self == SecondaryButtonStyle {
    static var secondary: SecondaryButtonStyle { .init() }
    static var secondaryDestructive: SecondaryButtonStyle { .init(destructive: true) }
}

/// Scrolling screen on the court canvas; use inside a NavigationStack.
struct Screen<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        ScrollView {
            VStack(spacing: 16) { content }
                .padding(.horizontal, 16).padding(.vertical, 12)
        }
        .modifier(DebugScroll())
        .background(Theme.canvas.ignoresSafeArea())
        .scrollBounceBehavior(.basedOnSize)
        .canvasNavigationBar()
    }
}

extension SessionSource {
    var icon: String {
        switch self {
        case .podStream, .podUpload: "sensor.tag.radiowaves.forward"
        case .watch: "applewatch"
        case .sample: "testtube.2"
        }
    }
    var label: String {
        switch self {
        case .podStream: "Pod, live"
        case .podUpload: "Pod, recorded"
        case .watch: "Apple Watch"
        case .sample: "Sample"
        }
    }
}

func clock(_ seconds: Double) -> String {
    let s = max(0, Int(seconds.rounded()))
    return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60) : String(format: "%d:%02d", s / 60, s % 60)
}

extension View {
    /// Keeps navigation titles legible over scrolled content with the system's soft top edge effect.
    func canvasNavigationBar() -> some View { scrollEdgeEffectStyle(.soft, for: .top) }
}

extension View {
    /// Lists and forms on the brand canvas instead of the system grey.
    func themedList() -> some View { scrollContentBackground(.hidden).background(Theme.canvas.ignoresSafeArea()) }
}
