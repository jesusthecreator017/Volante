// The oso Raspberry Pi server (app/server/server.mjs, deploy/pi/README.md): session exchange with the video
// pipeline and the Fitbit heart-rate proxy. Plain http on the LAN (port 8080); ATS allows local networking.

import Foundation
import Observation
import OsoKit

@MainActor
@Observable
final class PiServer {
    /// HTTPS from `tailscale serve` (tailnet only, a real certificate): encrypted traffic is never sent through iCloud Private
    /// Relay, which breaks plain http to the Pi on cellular. The old http address keeps working on the tailnet.
    static let defaultURL = "https://pi5server-1.tail05420e.ts.net:8444"
    static let oldDefaults = ["http://raspberrypi.local:8080", "http://pi5server-1.tail05420e.ts.net:8090", "http://100.103.93.45:8090"]
    var baseURL: String {
        didSet { UserDefaults.standard.set(baseURL, forKey: "oso.pi.url"); reachable = nil }
    }
    private(set) var reachable: Bool?
    /// Why the last check failed, in plain words (nil when it worked or has not run).
    private(set) var problem: String?
    /// The server lives on the Tailscale network (a *.ts.net or 100.x address), so the phone needs the Tailscale VPN on.
    var needsTailscale: Bool {
        let host = URL(string: baseURL)?.host ?? ""
        return host.hasSuffix(".ts.net") || host.hasPrefix("100.")
    }
    private(set) var busy = false
    private(set) var checking = false

    init() {
        // The team Pi over Tailscale (works anywhere Tailscale is on); the old LAN default is replaced.
        let saved = UserDefaults.standard.string(forKey: "oso.pi.url")
        baseURL = saved.map { Self.oldDefaults.contains($0) ? Self.defaultURL : $0 } ?? Self.defaultURL
    }

    private func url(_ path: String) throws -> URL {
        guard let u = URL(string: baseURL.trimmingCharacters(in: .init(charactersIn: "/ ")) + "/api/" + path) else {
            throw PiError.badURL
        }
        return u
    }

    private func request(_ path: String, method: String = "GET", body: Data? = nil, json: Bool = false) async throws -> (Data, Int) {
        var req = URLRequest(url: try url(path), timeoutInterval: 20)
        req.httpMethod = method
        req.httpBody = body
        if json { req.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, resp) = try await URLSession.shared.data(for: req)
        return (data, (resp as? HTTPURLResponse)?.statusCode ?? 0)
    }

    private func expectOK(_ r: (Data, Int)) throws -> Data {
        guard r.1 == 200 else {
            let msg = (try? JSONSerialization.jsonObject(with: r.0) as? [String: Any])?["error"] as? String
            throw PiError.http(r.1, msg ?? String(decoding: r.0.prefix(200), as: UTF8.self))
        }
        return r.0
    }

    func checkHealth() async {
        checking = true
        defer { checking = false }
        do {
            let d = try expectOK(await request("health"))
            reachable = (try? JSONSerialization.jsonObject(with: d) as? [String: Any])?["app"] as? String == "oso"
            problem = reachable == true ? nil : "Something answered at that address, but it isn't the Volante server."
        } catch {
            // Tailscale up but its DNS off ("Use Tailscale DNS"): the ts.net name doesn't resolve, the Pi's tailnet IP still works
            // (and IP connections are exempt from ATS). Switch to it when it answers.
            // Also when the name resolves publicly (Tailscale DNS off: the Funnel record answers, and 8444 is tailnet-only there).
            if let e = error as? URLError, [.cannotFindHost, .dnsLookupFailed, .timedOut, .cannotConnectToHost, .networkConnectionLost,
                                            .secureConnectionFailed].contains(e.code), baseURL == Self.defaultURL,
               let d = try? expectOK(await probe(Self.fallbackURL)),
               (try? JSONSerialization.jsonObject(with: d) as? [String: Any])?["app"] as? String == "oso" {
                baseURL = Self.fallbackURL
                reachable = true
                problem = nil
                return
            }
            reachable = false
            problem = explain(error)
        }
    }

    /// The team Pi's Tailscale IP (stable for the node), for phones whose Tailscale DNS is off. Plain http, since the
    /// certificate is for the name; it only travels inside the WireGuard tunnel.
    static let fallbackURL = "http://100.103.93.45:8090"

    private func probe(_ base: String) async throws -> (Data, Int) {
        let (data, resp) = try await URLSession.shared.data(for: URLRequest(url: URL(string: base + "/api/health")!, timeoutInterval: 8))
        return (data, (resp as? HTTPURLResponse)?.statusCode ?? 0)
    }

    private func explain(_ error: Error) -> String {
        let tail = needsTailscale ? " Open the Tailscale app on this iPhone, sign in and turn it on, then check again." : ""
        guard let e = error as? URLError else {
            if case PiError.badURL = error { return "That address isn't a valid URL." }
            return error.localizedDescription
        }
        switch e.code {
        case .cannotFindHost, .dnsLookupFailed:
            return needsTailscale ? "This iPhone can't find the Pi: it isn't connected to Tailscale." + tail
                                  : "This iPhone can't find that address. Check it, or use the Pi's IP address."
        case .timedOut, .cannotConnectToHost, .networkConnectionLost:
            return "The Pi didn't answer. It may be off, or the server isn't running." + (needsTailscale ? " Also check Tailscale is connected on this iPhone." : "")
        case .notConnectedToInternet:
            return "This iPhone is offline."
        case .appTransportSecurityRequiresSecureConnection:
            return "iOS blocked plain http to this address. Use the Pi's Tailscale name (pi5server-1.tail05420e.ts.net) or a local IP."
        default:
            return e.localizedDescription + tail
        }
    }

    // MARK: sessions

    /// PUT imu.bin then meta.json (the pipeline reads both; the server validates the imu.bin format).
    func upload(_ meta: SessionMeta, imu: URL) async throws {
        busy = true
        defer { busy = false }
        _ = try expectOK(await request("sessions/\(meta.id)/imu.bin", method: "PUT", body: try Data(contentsOf: imu)))
        _ = try expectOK(await request("sessions/\(meta.id)/meta.json", method: "PUT", body: try JSONEncoder.oso.encode(meta), json: true))
    }

    /// replay.bin from the video pipeline; nil while it hasn't been produced yet.
    func replay(_ id: String) async throws -> Data? {
        let r = try await request("sessions/\(id)/replay.bin")
        return r.1 == 404 ? nil : try expectOK(r)
    }

    // MARK: Fitbit proxy

    func fitbitConnected() async throws -> Bool {
        let d = try expectOK(await request("fitbit/status"))
        return (try JSONSerialization.jsonObject(with: d) as? [String: Any])?["connected"] as? Bool ?? false
    }

    func fitbitHandOver(clientId: String, refreshToken: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["clientId": clientId, "refreshToken": refreshToken])
        _ = try expectOK(await request("fitbit/token", method: "POST", body: body, json: true))
    }

    func fitbitDisconnect() async throws {
        _ = try expectOK(await request("fitbit/token", method: "DELETE"))
    }

    func heartRate(from: Date, to: Date) async throws -> [HeartRateSample] {
        let iso = ISO8601DateFormatter()
        let q = "from=\(iso.string(from: from))&to=\(iso.string(from: to))"
            .addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed.subtracting(.init(charactersIn: "+"))) ?? ""
        let d = try expectOK(await request("fitbit/heart-rate?\(q)"))
        return try JSONDecoder().decode([HeartRateSample].self, from: d)
    }
}

enum PiError: LocalizedError {
    case badURL, http(Int, String)
    var errorDescription: String? {
        switch self {
        case .badURL: "The Pi server address is not a valid URL"
        case .http(let c, let m): c == 0 ? "Can't reach the Pi server" : "Pi server \(c): \(m)"
        }
    }
}
