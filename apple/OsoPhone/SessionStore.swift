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
    /// What a list row shows, computed off the main actor once per session.
    struct RowSummary {
        var readable: Bool
        var swings = 0
        var durationS = 0.0
        var maxSpeed: Double?
        var longestRally = 0
        var mount = Mount.racquet
        var hits = 0
        var meanSpeed: Double?
        var activeS = 0.0
        var rallies = 0
        /// Stroke groups (experimental); nil for wrist sessions and while not classified yet.
        var breakdown: StrokeBreakdown?
    }
    /// Per-swing stroke results by session id (aligned with the analysis' swings); racquet sessions only.
    private(set) var strokes: [String: SessionStrokes] = [:]
    private(set) var rowSummaries: [String: RowSummary] = [:]
    /// Personal bests across the saved sessions (sample sessions excluded), refreshed whenever a summary lands or a session is deleted.
    private(set) var bests = PersonalBests()
    @ObservationIgnored private var unreadable: Set<String> = []
    /// Sessions being analysed right now, so a row and the personal-best pass never analyse the same file twice.
    @ObservationIgnored private var inFlight: Set<String> = []

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

    func replayURL(_ id: String) -> URL { folder(id).appendingPathComponent("replay.bin") }
    func hasReplay(_ id: String) -> Bool { FileManager.default.fileExists(atPath: replayURL(id).path) }
    func saveReplay(_ id: String, _ data: Data) throws { try data.write(to: replayURL(id), options: .atomic) }

    func delete(_ id: String) {
        try? FileManager.default.removeItem(at: folder(id))
        cache[id] = nil
        rowSummaries[id] = nil
        strokes[id] = nil
        unreadable.remove(id)
        reload()
        refreshBests()
    }

    func analysis(_ meta: SessionMeta) -> SessionAnalysis? {
        if let a = cache[meta.id] { return a }
        if unreadable.contains(meta.id) { return nil }
        guard let d = try? Data(contentsOf: imuURL(meta.id)), let cap = try? ImuFile.parse([UInt8](d)) else {
            unreadable.insert(meta.id)
            return nil
        }
        var a = analyzeSession(cap, settings: settings)
        if let anchor = meta.anchor, !meta.heartRate.isEmpty {
            a.swings = attachHeartRate(a.swings, HeartRateSeries(meta.heartRate), anchor: anchor)
        }
        cache[meta.id] = a
        return a
    }

    /// Fills `rowSummaries` for a list row; the parse and analysis run off the main actor.
    @MainActor
    func loadRowSummary(_ meta: SessionMeta) async {
        let id = meta.id
        if rowSummaries[id] != nil { return }
        if inFlight.contains(id) {
            while inFlight.contains(id) { try? await Task.sleep(for: .milliseconds(40)) }
            return
        }
        inFlight.insert(id)
        defer { inFlight.remove(id) }
        let url = imuURL(id), settings = settings
        let cacheURL = strokesURL(id)
        let (a, st) = await Task.detached(priority: .userInitiated) { () -> (SessionAnalysis?, SessionStrokes?) in
            guard let d = try? Data(contentsOf: url), let cap = try? ImuFile.parse([UInt8](d)) else { return (nil, nil) }
            let a = analyzeSession(cap, settings: settings)
            return (a, Self.strokeResults(a, cacheURL: cacheURL))
        }.value
        guard let a else {
            unreadable.insert(id)
            rowSummaries[id] = RowSummary(readable: false)
            refreshBests()
            return
        }
        if meta.heartRate.isEmpty, cache[id] == nil { cache[id] = a } // the detail screen reuses it
        rowSummaries[id] = RowSummary(
            readable: true, swings: a.summary.swingCount, durationS: a.summary.durationS,
            maxSpeed: a.summary.maxSpeed, longestRally: a.summary.longestRally, mount: a.mount,
            hits: a.summary.hitCount, meanSpeed: a.summary.meanSpeed, activeS: a.summary.activeS,
            rallies: a.summary.rallyCount, breakdown: st.map { StrokeBreakdown(swings: a.swings, results: $0.results) })
        strokes[id] = st
        refreshBests()
    }

    func strokesURL(_ id: String) -> URL { folder(id).appendingPathComponent("strokes.json") }

    /// The stroke results for an analysis: the cached file when it is for the current models and swings, else classified now
    /// (and cached). nil for wrist sessions (the models are racquet-only) or if the bundled models cannot load. Runs off the main actor.
    nonisolated static func strokeResults(_ a: SessionAnalysis, cacheURL: URL) -> SessionStrokes? {
        guard a.mount == .racquet, let c = StrokeClassifier.shared else { return nil }
        if let d = try? Data(contentsOf: cacheURL), let cached = try? JSONDecoder().decode(SessionStrokes.self, from: d), cached.valid(for: a, classifier: c) {
            return cached
        }
        let fresh = SessionStrokes(modelVersion: c.modelVersion, results: c.classify(a))
        if let d = try? JSONEncoder().encode(fresh) { try? d.write(to: cacheURL, options: .atomic) }
        return fresh
    }

    /// Fills `strokes` for a session whose detail is open (a no-op once the list row has done it).
    @MainActor
    func loadStrokes(_ meta: SessionMeta) async {
        guard strokes[meta.id] == nil, let a = analysis(meta), a.mount == .racquet else { return }
        let url = strokesURL(meta.id)
        let st = await Task.detached(priority: .userInitiated) { Self.strokeResults(a, cacheURL: url) }.value
        if strokes[meta.id] == nil { strokes[meta.id] = st }
    }

    /// Makes sure every saved session has a summary (each parsed and analysed off the main actor), so the personal bests are complete.
    @MainActor
    func loadAllSummaries() async {
        for m in sessions { await loadRowSummary(m) }
    }

    /// Only once every saved session has a summary, so a badge never appears and then vanishes while the rest load.
    private func refreshBests() {
        guard sessions.allSatisfy({ rowSummaries[$0.id] != nil }) else { return }
        let rows = sessions.compactMap { m in rowSummaries[m.id].flatMap { $0.readable ? PersonalBests.Entry(id: m.id, isSample: m.source == .sample, mount: $0.mount, maxSpeed: $0.maxSpeed, swings: $0.swings, longestRally: $0.longestRally) : nil } }
        bests = PersonalBests(rows)
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
