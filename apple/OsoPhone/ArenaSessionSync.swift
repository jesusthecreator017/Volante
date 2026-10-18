// Every finished session goes to the signed-in GitHub account (PUT /api/me/sessions/{id}), so it shows on the website under
// the same account. Idempotent on the server; the phone remembers per account what it already sent and retries the rest on
// the next sync (after a session, at sign-in, when the app comes forward).

import Foundation
import OsoKit

extension ArenaClient {
    private func syncedKey(_ login: String) -> String { "volante.arena.synced.\(login)" }

    /// sessions already on the signed-in account
    var syncedCount: Int { _ = syncTick; return user.map { UserDefaults.standard.stringArray(forKey: syncedKey($0.login))?.count ?? 0 } ?? 0 }

    /// Sends the sessions this account has not seen yet. Quiet on failure: the next sync tries again.
    func syncSessions(_ store: SessionStore) async {
        if me == nil { _ = try? await refreshMe() }
        guard let login = user?.login else { return }
        var done = Set(UserDefaults.standard.stringArray(forKey: syncedKey(login)) ?? [])
        for meta in store.sessions where meta.source != .sample && !done.contains(meta.id) {
            guard let body = await sessionBody(meta, store: store) else { continue }
            do {
                _ = try await requestJSON("PUT", "/api/me/sessions/\(meta.id)", body: body)
                done.insert(meta.id)
                UserDefaults.standard.set(Array(done), forKey: syncedKey(login))
                syncTick += 1
            } catch {
                return  // offline or signed out: stop, try again later
            }
        }
    }

    private func sessionBody(_ meta: SessionMeta, store: SessionStore) async -> Data? {
        guard let a = store.analysis(meta) else { return nil }
        let s = a.summary
        var o: [String: Any] = [
            "started_at": ISO8601DateFormatter().string(from: meta.createdAt),
            "sensor": a.mount == .wrist ? "watch" : meta.pairedSessionId != nil ? "pod+watch" : "pod",
            "duration_s": s.durationS, "swings": s.swingCount, "hits": s.hitCount,
        ]
        if let m = s.maxSpeed, a.mount != .wrist { o["best_kmh"] = (m * 3.6 * 10).rounded() / 10 }
        if let h = store.heartStats(meta) { o["avg_bpm"] = (h.avg * 10).rounded() / 10 }
        if let r = await store.report(meta, breakdown: nil), let j = try? JSONSerialization.jsonObject(with: Data(r.json().utf8)) {
            o["report"] = j
        }
        return try? JSONSerialization.data(withJSONObject: o)
    }
}
