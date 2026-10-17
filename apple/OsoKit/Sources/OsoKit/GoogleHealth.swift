// Fitbit data through the Google Health API (health.googleapis.com/v4), the successor of the Fitbit Web API
// (legacy API unsupported after Sept 30, 2026, off Oct 30, 2026). It has no accelerometer/gyroscope data types:
// Fitbit contributes heart rate (1 s resolution), calories and exercise sessions. This file builds requests;
// OsoPhone/FitbitLink.swift does OAuth (PKCE) and HTTP.
// Reference: developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/list

public enum GoogleHealth {
    public static let base = "https://health.googleapis.com/v4"
    public static let authorizeURL = "https://accounts.google.com/o/oauth2/v2/auth"
    public static let tokenURL = "https://oauth2.googleapis.com/token"
    public static let scopes = [
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly", // heart rate
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly", // exercise, calories, AZM
    ]

    public enum DataType: String, Sendable {
        case heartRate = "heart-rate"
        case exercise = "exercise"
        case totalCalories = "total-calories"
        case activeZoneMinutes = "active-zone-minutes"

        /// Field path of the record's time in list filters. Sample types filter on the sample time, interval
        /// types on the interval start (the documented example: `steps.interval.start_time`).
        var filterTime: String {
            switch self {
            case .heartRate: "heart_rate.sample_time.physical_time"
            case .exercise: "exercise.interval.start_time"
            case .totalCalories: "total_calories.interval.start_time"
            case .activeZoneMinutes: "active_zone_minutes.interval.start_time"
            }
        }
    }

    /// GET URL listing a data type's points in [from, to). pageSize max 10000 (exercise: 25).
    public static func listURL(_ type: DataType, from: EpochSeconds, to: EpochSeconds, pageSize: Int = 10000,
                               pageToken: String? = nil) -> String {
        let filter = "\(type.filterTime) >= \"\(rfc3339(from))\" AND \(type.filterTime) < \"\(rfc3339(to))\""
        var q = "filter=\(percentEncode(filter))&pageSize=\(type == .exercise ? min(pageSize, 25) : pageSize)"
        if let pageToken { q += "&pageToken=\(percentEncode(pageToken))" }
        return "\(base)/users/me/dataTypes/\(type.rawValue)/dataPoints?\(q)"
    }

    /// Authorization URL for the installed-app flow with PKCE (S256 challenge computed by the caller).
    public static func authorizeURL(clientId: String, redirectURI: String, codeChallenge: String, state: String) -> String {
        let params = [
            ("client_id", clientId), ("redirect_uri", redirectURI), ("response_type", "code"),
            ("scope", scopes.joined(separator: " ")), ("code_challenge", codeChallenge),
            ("code_challenge_method", "S256"), ("state", state), ("access_type", "offline"), ("prompt", "consent"),
        ]
        return authorizeURL + "?" + params.map { "\($0.0)=\(percentEncode($0.1))" }.joined(separator: "&")
    }

    /// Form body for the code -> token exchange (no client secret for iOS clients).
    public static func tokenBody(clientId: String, code: String, redirectURI: String, verifier: String) -> String {
        [("client_id", clientId), ("code", code), ("redirect_uri", redirectURI), ("grant_type", "authorization_code"),
         ("code_verifier", verifier)].map { "\($0.0)=\(percentEncode($0.1))" }.joined(separator: "&")
    }

    public static func refreshBody(clientId: String, refreshToken: String) -> String {
        [("client_id", clientId), ("refresh_token", refreshToken), ("grant_type", "refresh_token")]
            .map { "\($0.0)=\(percentEncode($0.1))" }.joined(separator: "&")
    }

    /// RFC 3339 UTC with milliseconds: 2026-10-16T21:02:04.123Z
    public static func rfc3339(_ t: EpochSeconds) -> String {
        let ms = Int64((t * 1000).rounded(.down))
        let secs = ms >= 0 ? ms / 1000 : (ms - 999) / 1000
        let milli = Int(ms - secs * 1000)
        let days = secs >= 0 ? secs / 86400 : (secs - 86399) / 86400
        let sod = Int(secs - days * 86400)
        let (y, m, d) = civil(days)
        func p(_ v: Int, _ n: Int) -> String { String(repeating: "0", count: max(0, n - String(v).count)) + String(v) }
        return "\(p(y, 4))-\(p(m, 2))-\(p(d, 2))T\(p(sod / 3600, 2)):\(p(sod / 60 % 60, 2)):\(p(sod % 60, 2)).\(p(milli, 3))Z"
    }

    /// Parses "2026-10-16T21:02:04Z" / "...04.123Z" / "...04.123456789Z" / "+hh:mm" offsets.
    public static func parseRFC3339(_ s: String) -> EpochSeconds? {
        let c = Array(s.utf8)
        func num(_ a: Int, _ n: Int) -> Int? {
            guard a + n <= c.count else { return nil }
            var v = 0
            for k in a..<(a + n) {
                guard c[k] >= 48 && c[k] <= 57 else { return nil }
                v = v * 10 + Int(c[k] - 48)
            }
            return v
        }
        guard c.count >= 20, let y = num(0, 4), let mo = num(5, 2), let d = num(8, 2), let h = num(11, 2),
              let mi = num(14, 2), let se = num(17, 2) else { return nil }
        var i = 19
        var frac = 0.0
        if i < c.count && c[i] == UInt8(ascii: ".") {
            i += 1
            var scale = 0.1
            while i < c.count, c[i] >= 48, c[i] <= 57 { frac += Double(c[i] - 48) * scale; scale /= 10; i += 1 }
        }
        var offset = 0
        if i < c.count && (c[i] == UInt8(ascii: "+") || c[i] == UInt8(ascii: "-")) {
            guard let oh = num(i + 1, 2), let om = num(i + 4, 2) else { return nil }
            offset = (oh * 3600 + om * 60) * (c[i] == UInt8(ascii: "+") ? 1 : -1)
        }
        let days = daysFromCivil(y, mo, d)
        return Double(days * 86400 + Int64(h * 3600 + mi * 60 + se - offset)) + frac
    }

    // Howard Hinnant's civil-date algorithms
    static func civil(_ z0: Int64) -> (Int, Int, Int) {
        let z = z0 + 719468
        let era = (z >= 0 ? z : z - 146096) / 146097
        let doe = z - era * 146097
        let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
        let mp = (5 * doy + 2) / 153
        let d = doy - (153 * mp + 2) / 5 + 1
        let m = mp < 10 ? mp + 3 : mp - 9
        return (Int(yoe + era * 400 + (m <= 2 ? 1 : 0)), Int(m), Int(d))
    }

    static func daysFromCivil(_ y0: Int, _ m: Int, _ d: Int) -> Int64 {
        let y = Int64(m <= 2 ? y0 - 1 : y0)
        let era = (y >= 0 ? y : y - 399) / 400
        let yoe = y - era * 400
        let mp = Int64(m > 2 ? m - 3 : m + 9)
        let doy = (153 * mp + 2) / 5 + Int64(d) - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        return era * 146097 + doe - 719468
    }

    /// RFC 3986 unreserved characters pass, everything else is %XX (UTF-8).
    public static func percentEncode(_ s: String) -> String {
        var out = ""
        for b in s.utf8 {
            switch b {
            case UInt8(ascii: "A")...UInt8(ascii: "Z"), UInt8(ascii: "a")...UInt8(ascii: "z"),
                 UInt8(ascii: "0")...UInt8(ascii: "9"), UInt8(ascii: "-"), UInt8(ascii: "."), UInt8(ascii: "_"),
                 UInt8(ascii: "~"):
                out.unicodeScalars.append(Unicode.Scalar(b))
            default:
                let hex = Array("0123456789ABCDEF")
                out += "%\(hex[Int(b >> 4)])\(hex[Int(b & 15)])"
            }
        }
        return out
    }
}
