// The Arena API over its public address (Tailscale Funnel; no Tailscale needed on the phone) and GitHub sign-in for the app:
// ASWebAuthenticationSession opens /auth/github/login?app=<sha256(verifier)>, the server returns volante://auth?code=..., and
// POST /api/app/session trades the code + verifier for a session id. The id lives only in the Keychain and goes out as the
// oso_session cookie; it never appears in a URL.

import AuthenticationServices
import CryptoKit
import Foundation
import Observation
import Security

@MainActor
@Observable
final class ArenaClient: NSObject, ASWebAuthenticationPresentationContextProviding {
    static let defaultBase = "https://pi5server-1.tail05420e.ts.net"
    var base: URL = URL(string: UserDefaults.standard.string(forKey: "volante.arena.url") ?? ArenaClient.defaultBase)!

    private(set) var me: ArenaMe?
    private(set) var signingIn = false
    var lastError: String?
    /// bumps when a session reaches the account (redraws the sync count)
    var syncTick = 0
    var user: ArenaUser? { me?.user }

    @ObservationIgnored private var sid: String? = Keychain.read("arena.session")
    @ObservationIgnored private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
    @ObservationIgnored private lazy var http: URLSession = {
        let c = URLSessionConfiguration.default
        c.httpCookieStorage = nil  // the session goes out as an explicit header only
        c.httpShouldSetCookies = false
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        c.timeoutIntervalForRequest = 20
        return URLSession(configuration: c)
    }()

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor { ASPresentationAnchor() }

    // MARK: requests

    enum Failure: LocalizedError {
        case http(Int, String)
        var errorDescription: String? { if case .http(_, let d) = self { d } else { nil } }
        var status: Int { if case .http(let s, _) = self { s } else { 0 } }
    }

    private func request(_ method: String, _ path: String, body: Data? = nil, contentType: String? = nil) async throws -> Data {
        var r = URLRequest(url: URL(string: path, relativeTo: base)!)
        r.httpMethod = method
        r.setValue("application/json", forHTTPHeaderField: "accept")
        if let sid { r.setValue("oso_session=\(sid)", forHTTPHeaderField: "cookie") }
        if method != "GET" {
            if me == nil { _ = try? await refreshMe() }
            if let csrf = me?.csrf { r.setValue(csrf, forHTTPHeaderField: "x-csrf-token") }
        }
        if let contentType { r.setValue(contentType, forHTTPHeaderField: "content-type") }
        r.httpBody = body
        let (data, resp) = try await http.data(for: r)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            let e = try? decoder.decode(ArenaError.self, from: data)
            if code == 401, sid != nil { forgetSession() }  // expired or revoked on the server
            throw Failure.http(code, e?.detail ?? "The Arena answered \(code).")
        }
        return data
    }

    func requestJSON(_ method: String, _ path: String, body: Data) async throws -> Data {
        try await request(method, path, body: body, contentType: "application/json")
    }

    func get<T: Decodable>(_ path: String, as: T.Type = T.self) async throws -> T {
        try decoder.decode(T.self, from: try await request("GET", path))
    }

    // MARK: endpoints

    func ladder(_ l: ArenaLadder, limit: Int = 50) async throws -> ArenaLadderPage {
        try await get("/api/ladder?limit=\(limit)" + (l == .twins ? "&ladder=twins" : ""))
    }
    func model(_ id: Int) async throws -> ArenaModelDetail { try await get("/api/models/\(id)") }
    func matches(model: Int? = nil, limit: Int = 20) async throws -> [ArenaMatch] {
        let page: ArenaMatchPage = try await get("/api/matches?limit=\(limit)" + (model.map { "&model=\($0)" } ?? ""))
        return page.items
    }
    func match(_ id: Int) async throws -> ArenaMatch { try await get("/api/matches/\(id)") }
    func currentTournament(_ l: ArenaLadder) async throws -> ArenaTournament? {
        do { return try await get("/api/tournaments/current" + (l == .twins ? "?ladder=twins" : "")) }
        catch let e as Failure where e.status == 404 { return nil }
    }
    func tournaments(_ l: ArenaLadder) async throws -> [ArenaTournament] { try await get("/api/tournaments" + (l == .twins ? "?ladder=twins" : "")) }
    func tournament(_ id: Int) async throws -> ArenaTournament { try await get("/api/tournaments/\(id)") }
    func myModels() async throws -> [ArenaModel] { (try await get("/api/models?owner=me", as: ArenaItems<ArenaModel>.self)).items }
    func viewerURL(match id: Int, dark: Bool) -> URL {
        URL(string: "/viewer/index.html?match=%2Fapi%2Fmatches%2F\(id)%2Freplay.bin&theme=\(dark ? "dark" : "light")", relativeTo: base)!
    }

    @discardableResult
    func refreshMe() async throws -> ArenaMe {
        let m: ArenaMe = try decoder.decode(ArenaMe.self, from: try await request("GET", "/api/me"))
        if m.user == nil, sid != nil { sid = nil; Keychain.delete("arena.session") }  // expired or signed out elsewhere
        me = m
        return m
    }

    /// Uploads a .safetensors submission (at most 1 MiB; the server checks it in its sandbox).
    func upload(_ file: URL) async throws -> ArenaUploadResult {
        let ok = file.startAccessingSecurityScopedResource()
        defer { if ok { file.stopAccessingSecurityScopedResource() } }
        let data = try Data(contentsOf: file)
        guard data.count <= 1 << 20 else { throw Failure.http(413, "That file is over the 1 MiB limit (models are about 300 KB).") }
        guard file.pathExtension.lowercased() == "safetensors" else { throw Failure.http(400, "Choose a .safetensors file exported with the kit.") }
        let boundary = "volante-\(UUID().uuidString)"
        var body = Data()
        body.append(Data("--\(boundary)\r\ncontent-disposition: form-data; name=\"file\"; filename=\"\(file.lastPathComponent.replacingOccurrences(of: "\"", with: ""))\"\r\ncontent-type: application/octet-stream\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        return try decoder.decode(ArenaUploadResult.self, from: try await request("POST", "/api/models", body: body, contentType: "multipart/form-data; boundary=\(boundary)"))
    }

    func retire(_ id: Int) async throws { _ = try await request("DELETE", "/api/models/\(id)") }

    // MARK: sign-in

    func signIn() async {
        signingIn = true
        defer { signingIn = false }
        lastError = nil
        var bytes = [UInt8](repeating: 0, count: 48)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        let verifier = Data(bytes).base64URL
        let challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
        let url = URL(string: "/auth/github/login?app=\(challenge)&next=%2Farena", relativeTo: base)!
        do {
            let callback: URL = try await withCheckedThrowingContinuation { k in
                let s = ASWebAuthenticationSession(url: url, callback: .customScheme("volante")) { url, err in
                    if let url { k.resume(returning: url) } else { k.resume(throwing: err ?? URLError(.cancelled)) }
                }
                s.presentationContextProvider = self
                s.start()
            }
            let q = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
            if let reason = q.first(where: { $0.name == "error" })?.value {
                lastError = ArenaSignInReason.text(reason)
                return
            }
            guard let code = q.first(where: { $0.name == "code" })?.value else { lastError = "GitHub sign-in returned no code."; return }
            let body = try JSONSerialization.data(withJSONObject: ["code": code, "verifier": verifier])
            let s: ArenaAppSession = try decoder.decode(ArenaAppSession.self, from: try await request("POST", "/api/app/session", body: body, contentType: "application/json"))
            sid = s.session
            Keychain.write("arena.session", s.session)
            try await refreshMe()
        } catch let e as ASWebAuthenticationSessionError where e.code == .canceledLogin {
            return
        } catch {
            lastError = error.localizedDescription
        }
    }

    func signOut() async {
        _ = try? await request("POST", "/auth/logout")
        forgetSession()
        _ = try? await refreshMe()
    }

    private func forgetSession() {
        sid = nil
        Keychain.delete("arena.session")
        if me?.user != nil { me?.user = nil }
    }
}

/// The session id in the Keychain (this device only, available after the first unlock).
enum Keychain {
    private static func query(_ key: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "volante", kSecAttrAccount as String: key]
    }
    static func read(_ key: String) -> String? {
        var q = query(key)
        q[kSecReturnData as String] = true
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        return String(data: d, encoding: .utf8)
    }
    static func write(_ key: String, _ value: String) {
        delete(key)
        var q = query(key)
        q[kSecValueData as String] = Data(value.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(q as CFDictionary, nil)
    }
    static func delete(_ key: String) { SecItemDelete(query(key) as CFDictionary) }
}
