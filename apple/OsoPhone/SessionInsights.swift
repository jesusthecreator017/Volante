// Wording for a swing (the pure insight logic lives in OsoKit: PersonalBests, SwingTrace).

import Foundation
import OsoKit

// MARK: swing wording

extension Swing {
    /// "on peak", "early 12 ms", "late 8 ms"; nil without a detected impact.
    var impactLabel: String? {
        guard let timing else { return nil }
        guard timing != "on peak", let d = impactVsPeakMs else { return timing }
        return "\(timing) \(Int(abs(d).rounded())) ms"
    }

    var strokeTitle: String { stroke.rawValue.capitalized }

    /// "Swing 3, smash, 19.0 metres per second, impact on peak"
    var spoken: String {
        let impact: String
        switch (timing, impactLabel) {
        case (nil, _), (_, nil): impact = "no impact detected"
        case ("on peak", _): impact = "impact on peak"
        case (_, let l?): impact = "impact \(l.replacingOccurrences(of: " ms", with: " milliseconds"))"
        }
        return String(format: "Swing %d, %@, %.1f metres per second, ", index, stroke.rawValue, headSpeed) + impact
    }

    /// The analysis' confidence reasons in plain words.
    var plainReasons: [String] {
        confidenceReasons.map { r in
            if r.hasPrefix("gyro saturated") { return "The sensor hit its limit during the swing, so the true peak is higher than shown." }
            if r.contains("packets lost inside") {
                let n = r.split(separator: " ").first.map(String.init) ?? "Some"
                return "\(n) radio packets were lost during this swing, so some detail is missing."
            }
            if r.hasPrefix("a gap of") { return "A stretch of data went missing near the peak, so the impact may have been missed." }
            if r.hasPrefix("orientation") { return "The racquet's angle at impact is a best guess." }
            return r
        }
    }
}
