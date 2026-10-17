// oso "courtside broadcast" look: court green, shuttle orange, scoreboard numbers. Shared building blocks for the three tabs.

import OsoKit
import SwiftUI

enum Theme {
    /// Page background: a faint green wash in light mode, near-black court in dark mode.
    static let canvas = Color(light: 0xEBF3EE, dark: 0x0C1511)
    static let card = Color(light: 0xFFFFFF, dark: 0x17241E)
    /// Header banners (white text on top, in both appearances).
    static let courtTop = Color(light: 0x1E7A54, dark: 0x1A6445)
    static let courtBottom = Color(light: 0x0F4A33, dark: 0x0B3526)
    /// Text and icons in court green on a card.
    static let courtInk = Color(light: 0x0F4A33, dark: 0x6FD3A1)
    /// Fill behind white button text: 4.94:1 (light) / 5.5:1 (dark). The accent itself is only for tints and icons.
    static let action = Color(light: 0xC2490A, dark: 0xB84208)
    static let good = Color(light: 0x1B8A4B, dark: 0x4CD38A)
    static let bad = Color(light: 0xC62828, dark: 0xFF6B6B)
    /// Destructive button fill under white text (>= 4.5:1 in both appearances).
    static let badFill = Color(light: 0xC62828, dark: 0xB3261E)

    static let banner = LinearGradient(colors: [courtTop, courtBottom], startPoint: .topLeading, endPoint: .bottomTrailing)

    static func stroke(_ s: StrokeType) -> Color {
        switch s {
        case .smash: Color(light: 0xE5461C, dark: 0xFF6A3D)
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
                    Text(title).font(.headline).foregroundStyle(Theme.courtInk)
                }
                .accessibilityAddTraits(.isHeader)
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(Theme.courtInk.opacity(0.10)))
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
            .padding(18)
            .background(Theme.banner, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
            .foregroundStyle(.white)
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    var destructive = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity, minHeight: 28)
            .padding(.vertical, 12).padding(.horizontal, 14)
            .foregroundStyle(.white)
            .background((destructive ? Theme.badFill : Theme.action).opacity(enabled ? (configuration.isPressed ? 0.8 : 1) : 0.35),
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    var destructive = false
    func makeBody(configuration: Configuration) -> some View {
        let tint = destructive ? Theme.bad : Theme.courtInk
        configuration.label
            .font(.headline)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity, minHeight: 28)
            .padding(.vertical, 12).padding(.horizontal, 14)
            .foregroundStyle(tint.opacity(enabled ? 1 : 0.4))
            .background(tint.opacity(configuration.isPressed ? 0.2 : 0.1), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
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
    /// Keeps navigation titles legible over scrolled content: the system's soft top edge effect on iOS 26+,
    /// an opaque canvas bar before that (which hides the large title, hence the split).
    @ViewBuilder func canvasNavigationBar() -> some View {
        if #available(iOS 26.0, *) {
            scrollEdgeEffectStyle(.soft, for: .top)
        } else {
            toolbarBackground(Theme.canvas, for: .navigationBar).toolbarBackground(.visible, for: .navigationBar)
        }
    }
}
