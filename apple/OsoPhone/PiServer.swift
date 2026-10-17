// The oso Raspberry Pi server (app/server/server.mjs, deploy/pi/README.md): session exchange with the video
// pipeline and the Fitbit heart-rate proxy. Plain http on the LAN (port 8080); ATS allows local networking.

import Foundation
import Observation
import OsoKit

@MainActor
@Observable
final class PiServer {
    var baseURL: String {
        didSet { UserDefaults.standard.set(baseURL, forKey: "oso.pi.url"); reachable = nil }
    }
    private(set) var reachable: Bool?
    private(set) var busy = false

    init() {
        baseURL = UserDefaults.standard.string(forKey: "oso.pi.url") ?? "http://raspberrypi.local:8080"
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
        do {
            let d = try expectOK(await request("health"))
            reachable = (try? JSONSerialization.jsonObject(with: d) as? [String: Any])?["app"] as? String == "oso"
        } catch {
            reachable = false
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
