// Sessions on the phone: Documents/Sessions/<id>/{imu.bin, meta.json}. Same imu.bin as the pod, oso-imu and the
// web app, so files can be shared (AirDrop / Files) into the rest of the pipeline.

import Foundation
import Observation
import OsoKit

@Observable
final class SessionStore {
    private(set) var sessions: [SessionMeta] = []
    @ObservationIgnored private var cache: [String: SessionAnalysis] = [:]
    var settings = AnalysisSettings()

    private let root: URL = {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dir = docs.appendingPathComponent("Sessions", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    init() { reload() }

    func reload() {
        let dirs = (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
        sessions = dirs.compactMap { dir in
            guard let d = try? Data(contentsOf: dir.appendingPathComponent("meta.json")) else { return nil }
            return try? JSONDecoder.oso.decode(SessionMeta.self, from: d)
        }.sorted { $0.createdAt > $1.createdAt }
    }

    func folder(_ id: String) -> URL { root.appendingPathComponent(id, isDirectory: true) }
    func imuURL(_ id: String) -> URL { folder(id).appendingPathComponent("imu.bin") }

    @discardableResult
    func save(imu: [UInt8], meta: SessionMeta) throws -> SessionMeta {
        _ = try ImuFile.parse(imu) // never store a file the rest of the pipeline can't read
        let dir = folder(meta.id)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data(imu).write(to: imuURL(meta.id), options: .atomic)
        try writeMeta(meta)
        reload()
        return meta
    }

    /// Imports a file received from the watch (moved out of WatchConnectivity's temporary location by the caller).
    func importFile(_ url: URL, meta: SessionMeta) throws {
        try save(imu: [UInt8](try Data(contentsOf: url)), meta: meta)
    }

    func writeMeta(_ meta: SessionMeta) throws {
        try JSONEncoder.oso.encode(meta).write(to: folder(meta.id).appendingPathComponent("meta.json"), options: .atomic)
        cache[meta.id] = nil
        if let i = sessions.firstIndex(where: { $0.id == meta.id }) { sessions[i] = meta }
    }

    func delete(_ id: String) {
        try? FileManager.default.removeItem(at: folder(id))
        cache[id] = nil
        reload()
    }

    func analysis(_ meta: SessionMeta) -> SessionAnalysis? {
        if let a = cache[meta.id] { return a }
        guard let d = try? Data(contentsOf: imuURL(meta.id)), let cap = try? ImuFile.parse([UInt8](d)) else { return nil }
        var a = analyzeSession(cap, settings: settings)
        if let anchor = meta.anchor, !meta.heartRate.isEmpty {
            a.swings = attachHeartRate(a.swings, HeartRateSeries(meta.heartRate), anchor: anchor)
        }
        cache[meta.id] = a
        return a
    }

    /// The bundled synthetic session (same file as the web app's sample).
    func importSample() throws {
        guard let url = Bundle.main.url(forResource: "imu", withExtension: "bin") else { return }
        try save(imu: [UInt8](try Data(contentsOf: url)), meta: SessionMeta(
            id: newSessionId(), name: "Sample session (synthetic)", createdAt: Date(), source: .sample))
    }
}

extension JSONEncoder {
    static let oso: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        return e
    }()
}

extension JSONDecoder {
    static let oso: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()
}
