// Session summaries to the web dashboard (docs/superpowers/specs/2026-10-18-web-dashboard-design.md §4): when signed in to the
// Arena and "Show my sessions on volante web" is on, every saved pod session's volante-report/1 goes to PUT /api/me/sessions/{id}
// (a backfill of the older ones, oldest first, 15 a minute at most) and the style profile to PUT /api/me/style. Summaries only:
// no raw IMU, no device name. The queue (OsoKit WebSyncQueue) is saved per Arena user, so a quit or sign-out resumes cleanly.

import CryptoKit
import Foundation
import Observation
import OsoKit

@MainActor
@Observable
final class WebSync {
    private(set) var state = WebSyncQueue()
    private(set) var deleting = false
    @ObservationIgnored private let store: SessionStore
    @ObservationIgnored private let arena: ArenaClient
    @ObservationIgnored private var userId: Int?
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var runId = 0

    init(store: SessionStore, arena: ArenaClient) {
        self.store = store; self.arena = arena
        store.onDelete = { [weak self] id in self?.sessionDeleted(id) }
    }

    /// The user deleted a session on the phone: queue the DELETE of its server copy (even with sync off, or signed out since this
    /// account's queue was loaded: it goes out at the next sign-in).
    func sessionDeleted(_ id: String) {
        _ = signedIn()
        guard userId != nil else { return }
        state.localDelete(id)
        save()
        kick()
    }

    /// The status line under the toggle.
    var status: String {
        if let e = state.lastError { return e }
        if !state.pending.isEmpty { return "Syncing · \(state.pending.count) to go" }
        return "Synced to the web · \(state.syncedCount) session\(state.syncedCount == 1 ? "" : "s")"
    }

    func setEnabled(_ on: Bool) {
        guard signedIn() else { return }
        state.setEnabled(on)
        save()
        if !on { task?.cancel() }  // wakes a run waiting out a backoff, which then ends
        kick()
    }

    /// Looks for new or changed sessions and sends them; cheap, call it whenever sessions or the sign-in change.
    func kick() {
        guard signedIn() else { return }
        // safety net only (deletes come from sessionDeleted); never infers deletes from an empty or half-read store
        state.reconcile(local: Set(store.sessions.map(\.id)), loaded: store.loadedCleanly)
        guard state.enabled || !state.pendingDeletes.isEmpty else { save(); return }
        // ponytail: pod sessions only; the app builds no wrist report yet (hand speed is not head speed). Add watch with source "watch".
        state.enqueue(store.sessions.filter { $0.source != .watch }.map {
            .init(id: $0.id, startedAt: $0.createdAt.timeIntervalSince1970, fingerprint: "hr\($0.heartRate.count)", isSample: $0.source == .sample)
        })
        save()
        if task == nil || task!.isCancelled {
            runId += 1
            let mine = runId
            task = Task { await run(); if runId == mine { task = nil } }
        }
    }

    /// "Delete my web data": stops syncing first (so no upload lands after the delete), then deletes everything on the server.
    func deleteWebData() async throws {
        guard signedIn() else { return }
        deleting = true
        defer { deleting = false }
        state.setEnabled(false)
        save()
        task?.cancel()
        await task?.value
        try await arena.deleteWebData()
        state.forget()
        save()
    }

    private func run() async {
        while arena.user != nil, !Task.isCancelled {
            let now = Date().timeIntervalSince1970
            if let id = state.nextDelete(now: now) {
                await delete(id)
                save()
                continue
            }
            guard let id = state.next(now: now) else {
                if state.pending.isEmpty, state.pendingDeletes.isEmpty { await sendStyle(); return }
                try? await Task.sleep(for: .seconds(max(0.5, state.notBefore - now)))
                continue
            }
            await send(id)
            save()
        }
    }

    private func send(_ id: String) async {
        guard let meta = store.sessions.first(where: { $0.id == id }) else { state.drop(id); return }
        await store.loadRowSummary(meta)
        guard let report = await store.report(meta, breakdown: store.rowSummaries[id]?.breakdown, forWeb: true) else { state.drop(id); return }
        let body = report.uploadBody()
        let now = { Date().timeIntervalSince1970 }
        guard body.count <= 16384 else {
            state.failed(id, now: now(), permanent: true, error: "A session is too long to show on the web.")
            return
        }
        do {
            try await arena.putJSON("/api/me/sessions/\(Self.webId(id))", body)
            state.succeeded(id, now: now())
        } catch let e as ArenaClient.Failure {
            if Task.isCancelled { return }
            if e.status == 403 { _ = try? await arena.refreshMe() }  // a stale CSRF token
            // 400 invalid_report / too_many_sessions and 413 will not change on a retry
            state.failed(id, now: now(), retryAfter: e.retryAfter, permanent: e.status == 400 || e.status == 413,
                         error: e.localizedDescription)
        } catch {
            if Self.cancelled(error) { return }  // sync turned off or the web data deleted mid-upload: not a failure
            state.failed(id, now: now(), error: "Can't reach volante web. Will retry.")
        }
    }

    private func delete(_ id: String) async {
        let now = { Date().timeIntervalSince1970 }
        do {
            try await arena.deleteSession(Self.webId(id))
            state.deleted(id, now: now())
        } catch let e as ArenaClient.Failure where e.status == 404 {
            state.deleted(id, now: now())  // already gone
        } catch let e as ArenaClient.Failure {
            if Task.isCancelled { return }
            if e.status == 403 { _ = try? await arena.refreshMe() }
            state.failed(id, now: now(), retryAfter: e.retryAfter, error: e.localizedDescription)
        } catch {
            if Self.cancelled(error) { return }
            state.failed(id, now: now(), error: "Can't reach volante web. Will retry.")
        }
    }

    private static func cancelled(_ e: Error) -> Bool {
        Task.isCancelled || e is CancellationError || (e as? URLError)?.code == .cancelled
    }

    /// The style profile, again whenever the set of pod sessions changed. One try per run; a failure retries on the next kick.
    private func sendStyle() async {
        let pods = store.sessions.filter { $0.source == .podStream || $0.source == .podUpload }.map(\.id).sorted()
        let basis = Self.sha(pods.joined(separator: ","))
        guard !pods.isEmpty, state.needsStyle(basis) else { return }
        let wait = state.notBefore - Date().timeIntervalSince1970  // same pacing as the sessions
        if wait > 0 { try? await Task.sleep(for: .seconds(wait)) }
        guard !Task.isCancelled, state.enabled else { return }
        if case .success(let p) = await store.styleProfile() {
            guard (try? await arena.putJSON("/api/me/style", Data(p.json().utf8))) != nil else { return }
        }
        state.styleSent(basis)  // sent, or not enough play for a profile yet (retried when the sessions change)
        save()
    }

    /// The app's own session id when the server accepts it (ours are "20261018-153000-ab12"), else a stable hash of it.
    static func webId(_ id: String) -> String { WebSyncQueue.isValidId(id) ? id : "h" + String(sha(id).prefix(40)) }
    private static func sha(_ s: String) -> String { SHA256.hash(data: Data(s.utf8)).map { String(format: "%02x", $0) }.joined() }

    /// Loads the signed-in user's queue (each Arena account has its own); false when signed out.
    private func signedIn() -> Bool {
        guard let uid = arena.user?.id else { return false }
        if uid != userId { load(uid) }
        return true
    }

    private var key: String { "volante.websync.\(userId ?? 0)" }
    private func load(_ uid: Int) {
        userId = uid
        state = UserDefaults.standard.data(forKey: key).flatMap { try? JSONDecoder().decode(WebSyncQueue.self, from: $0) } ?? WebSyncQueue()
    }
    private func save() {
        guard userId != nil, let d = try? JSONEncoder().encode(state) else { return }
        UserDefaults.standard.set(d, forKey: key)
    }
}
