// Fitbit heart rate. The data comes from the Google Health API (the Fitbit Web API's successor; no motion data
// exists there), but every API call runs on the Raspberry Pi (PiServer, /api/fitbit/*). The phone only does the
// Google sign-in, because that needs a browser and the user: OAuth 2.0 installed-app flow with PKCE in
// ASWebAuthenticationSession, then the refresh token goes to the Pi once and is not kept on the phone.
// Setup: an iOS OAuth client in Google Cloud with the Google Health API enabled and you as a test user;
// GOOGLE_CLIENT_ID and GOOGLE_REDIRECT_SCHEME in Config.xcconfig.

import AuthenticationServices
import CryptoKit
import Foundation
import Observation
import OsoKit
import Security

@MainActor
@Observable
final class FitbitLink: NSObject, ASWebAuthenticationPresentationContextProviding {
    /// as reported by the Pi; nil = not checked yet
    private(set) var connected: Bool?
    private(set) var busy = false
    var lastError: String?
    @ObservationIgnored let pi: PiServer

    init(pi: PiServer) {
        self.pi = pi
        super.init()
    }

    private var clientId: String { Bundle.main.object(forInfoDictionaryKey: "GoogleClientID") as? String ?? "" }
    /// com.googleusercontent.apps.<id>: the reversed client ID, as Google issues it for iOS clients
    private var scheme: String { clientId.split(separator: ".").reversed().joined(separator: ".") }
    private var redirectURI: String { "\(scheme):/oauthredirect" }
    var configured: Bool { clientId.hasSuffix(".apps.googleusercontent.com") }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor { ASPresentationAnchor() }

    func refreshStatus() async {
        do { connected = try await pi.fitbitConnected(); lastError = nil } catch { connected = nil; lastError = error.localizedDescription }
    }

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
                s.start()
            }
            let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
            guard items.first(where: { $0.name == "state" })?.value == state,
                  let code = items.first(where: { $0.name == "code" })?.value else {
                throw FitbitError.oauth(items.first(where: { $0.name == "error" })?.value ?? "no code")
            }
            let refresh = try await exchange(GoogleHealth.tokenBody(clientId: clientId, code: code, redirectURI: redirectURI, verifier: verifier))
            try await pi.fitbitHandOver(clientId: clientId, refreshToken: refresh)
            connected = true
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func disconnect() async {
        do { try await pi.fitbitDisconnect(); connected = false } catch { lastError = error.localizedDescription }
    }

    /// Code -> tokens; returns the refresh token (access_type=offline + prompt=consent make Google send one).
    private func exchange(_ body: String) async throws -> String {
        var req = URLRequest(url: URL(string: GoogleHealth.tokenURL)!)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data(body.utf8)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200,
              let j = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let refresh = j["refresh_token"] as? String else {
            throw FitbitError.oauth(String(decoding: data, as: UTF8.self))
        }
        return refresh
    }

    static let autoAttachKey = "volante.fitbit.auto"
    /// Sessions the last pass tried, so one that Fitbit has no data for yet is asked again only after a while.
    @ObservationIgnored private var tried: [String: Date] = [:]
    private(set) var lastAttached: Date?

    /// Adds Fitbit heart rate to recent sessions that have a clock anchor and none yet. Fitbit only has the data once the
    /// tracker has synced, so this runs on save, when the app becomes active, and when Sessions opens.
    func backfill(_ store: SessionStore) async {
        let auto = UserDefaults.standard.object(forKey: Self.autoAttachKey) as? Bool ?? true
        guard auto, connected == true, !busy else { return }
        let now = Date()
        for meta in store.sessions where meta.source != .sample && meta.heartRate.isEmpty && meta.anchor != nil
            && now.timeIntervalSince(meta.createdAt) < 7 * 86400 && now.timeIntervalSince(tried[meta.id] ?? .distantPast) > 600 {
            tried[meta.id] = now
            guard let anchor = meta.anchor, let a = store.analysis(meta) else { continue }
            let from = Date(timeIntervalSince1970: anchor.wall(a.t0) - 60), to = Date(timeIntervalSince1970: anchor.wall(a.t1) + 60)
            guard let hr = try? await heartRate(from: from, to: to), !hr.isEmpty else { continue }
            var m = meta
            m.heartRate = hr
            m.heartRateSource = "Fitbit"
            try? store.writeMeta(m)
            lastAttached = now
        }
    }

    /// Heart rate for a time window, fetched by the Pi.
    func heartRate(from: Date, to: Date) async throws -> [HeartRateSample] {
        try await pi.heartRate(from: from, to: to)
    }

    private static func randomURLSafe(_ n: Int) -> String {
        var bytes = [UInt8](repeating: 0, count: n)
        _ = SecRandomCopyBytes(kSecRandomDefault, n, &bytes)
        return Data(bytes).base64URL
    }
}

enum FitbitError: LocalizedError {
    case oauth(String)
    var errorDescription: String? {
        switch self {
        case .oauth(let m): "Google sign-in failed: \(m.prefix(200))"
        }
    }
}

extension Data {
    var base64URL: String {
        base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
