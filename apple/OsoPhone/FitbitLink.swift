// Fitbit heart rate through the Google Health API (the Fitbit Web API's successor; no motion data exists there).
// OAuth 2.0 installed-app flow with PKCE in ASWebAuthenticationSession; tokens in the Keychain.
// Setup: an iOS OAuth client in Google Cloud with the Google Health API enabled and you as a test user;
// GOOGLE_CLIENT_ID in Config.xcconfig (Info.plist reads it), its reversed form registered as a URL scheme.

import AuthenticationServices
import CryptoKit
import Foundation
import Observation
import OsoKit
import Security

@Observable
final class FitbitLink: NSObject, ASWebAuthenticationPresentationContextProviding {
    private(set) var connected = false
    private(set) var busy = false
    var lastError: String?

    @ObservationIgnored private var accessToken: String?
    @ObservationIgnored private var expiry = Date.distantPast

    private var clientId: String { Bundle.main.object(forInfoDictionaryKey: "GoogleClientID") as? String ?? "" }
    /// com.googleusercontent.apps.<id> : the reversed client ID, as Google issues it for iOS clients
    private var scheme: String { clientId.split(separator: ".").reversed().joined(separator: ".") }
    private var redirectURI: String { "\(scheme):/oauthredirect" }
    var configured: Bool { clientId.hasSuffix(".apps.googleusercontent.com") }

    override init() {
        super.init()
        connected = Keychain.get("oso.google.refresh") != nil
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor { ASPresentationAnchor() }

    // MARK: OAuth

    @MainActor
    func connect() async {
        guard configured else { lastError = "Set GOOGLE_CLIENT_ID in Config.xcconfig first"; return }
        busy = true
        defer { busy = false }
        let verifier = Self.randomURLSafe(64)
        let challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
        let state = Self.randomURLSafe(16)
        let url = URL(string: GoogleHealth.authorizeURL(clientId: clientId, redirectURI: redirectURI,
                                                        codeChallenge: challenge, state: state))!
        do {
            let callback: URL = try await withCheckedThrowingContinuation { k in
                let s = ASWebAuthenticationSession(url: url, callbackURLScheme: scheme) { url, err in
                    if let url { k.resume(returning: url) } else { k.resume(throwing: err ?? URLError(.cancelled)) }
                }
                s.presentationContextProvider = self
                s.prefersEphemeralWebBrowserSession = false
                s.start()
            }
            let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
            guard items.first(where: { $0.name == "state" })?.value == state,
                  let code = items.first(where: { $0.name == "code" })?.value else {
                throw FitbitError.oauth(items.first(where: { $0.name == "error" })?.value ?? "no code")
            }
            try await token(GoogleHealth.tokenBody(clientId: clientId, code: code, redirectURI: redirectURI, verifier: verifier))
            connected = true
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func disconnect() {
        Keychain.delete("oso.google.refresh")
        accessToken = nil
        connected = false
    }

    private func token(_ body: String) async throws {
        var req = URLRequest(url: URL(string: GoogleHealth.tokenURL)!)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data(body.utf8)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200,
              let j = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let access = j["access_token"] as? String else {
            throw FitbitError.oauth(String(decoding: data, as: UTF8.self))
        }
        accessToken = access
        expiry = Date().addingTimeInterval((j["expires_in"] as? Double ?? 3600) - 60)
        if let refresh = j["refresh_token"] as? String { Keychain.set("oso.google.refresh", refresh) }
    }

    private func validToken() async throws -> String {
        if let t = accessToken, Date() < expiry { return t }
        guard let refresh = Keychain.get("oso.google.refresh") else { throw FitbitError.notConnected }
        try await token(GoogleHealth.refreshBody(clientId: clientId, refreshToken: refresh))
        return accessToken!
    }

    // MARK: data

    /// Heart-rate samples (1 s resolution where the tracker recorded them) for [from, to], all pages.
    func heartRate(from: Date, to: Date) async throws -> [HeartRateSample] {
        busy = true
        defer { busy = false }
        var out: [HeartRateSample] = []
        var page: String?
        repeat {
            let url = GoogleHealth.listURL(.heartRate, from: from.timeIntervalSince1970, to: to.timeIntervalSince1970, pageToken: page)
            var req = URLRequest(url: URL(string: url)!)
            req.setValue("Bearer \(try await validToken())", forHTTPHeaderField: "Authorization")
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
                throw FitbitError.http((resp as? HTTPURLResponse)?.statusCode ?? 0, String(decoding: data, as: UTF8.self))
            }
            let j = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            for p in j["dataPoints"] as? [[String: Any]] ?? [] { if let s = Self.heartRateSample(p) { out.append(s) } }
            page = (j["nextPageToken"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        } while page != nil
        return out.sorted { $0.t < $1.t }
    }

    /// A DataPoint's heart rate: { heartRate: { sampleTime: { physicalTime }, beatsPerMinute: "72" } }. The
    /// record is looked up by its fields, not its key, to tolerate naming differences between API versions.
    static func heartRateSample(_ point: [String: Any]) -> HeartRateSample? {
        for case let rec as [String: Any] in point.values {
            guard let bpmAny = rec["beatsPerMinute"],
                  let time = (rec["sampleTime"] as? [String: Any])?["physicalTime"] as? String,
                  let t = GoogleHealth.parseRFC3339(time) else { continue }
            let bpm = (bpmAny as? Double) ?? Double(bpmAny as? String ?? "")
            if let bpm { return HeartRateSample(t: t, bpm: bpm) }
        }
        return nil
    }

    private static func randomURLSafe(_ n: Int) -> String {
        var bytes = [UInt8](repeating: 0, count: n)
        _ = SecRandomCopyBytes(kSecRandomDefault, n, &bytes)
        return Data(bytes).base64URL
    }
}

enum FitbitError: LocalizedError {
    case notConnected, oauth(String), http(Int, String)
    var errorDescription: String? {
        switch self {
        case .notConnected: "Fitbit is not connected"
        case .oauth(let m): "Google sign-in failed: \(m)"
        case .http(let c, let m): "Google Health API HTTP \(c): \(m.prefix(200))"
        }
    }
}

extension Data {
    var base64URL: String {
        base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// Minimal generic-password Keychain storage.
enum Keychain {
    static func set(_ key: String, _ value: String) {
        delete(key)
        SecItemAdd([kSecClass: kSecClassGenericPassword, kSecAttrAccount: key, kSecValueData: Data(value.utf8),
                    kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlock] as CFDictionary, nil)
    }
    static func get(_ key: String) -> String? {
        var out: AnyObject?
        let q = [kSecClass: kSecClassGenericPassword, kSecAttrAccount: key, kSecReturnData: true] as CFDictionary
        guard SecItemCopyMatching(q, &out) == errSecSuccess, let d = out as? Data else { return nil }
        return String(data: d, encoding: .utf8)
    }
    static func delete(_ key: String) {
        SecItemDelete([kSecClass: kSecClassGenericPassword, kSecAttrAccount: key] as CFDictionary)
    }
}
