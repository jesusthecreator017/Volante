// The web-dashboard upload queue (docs/superpowers/specs/2026-10-18-web-dashboard-design.md §4): which session summaries still
// have to go to `PUT /api/me/sessions/{id}`, oldest first, paced, with capped backoff. Pure state, Codable so the app persists it
// and resumes after a quit or a sign-out without sending a session twice (the PUT is idempotent by id anyway).

import Foundation

public struct WebSyncQueue: Codable, Equatable, Sendable {
    public struct Session: Codable, Equatable, Sendable {
        public var id: String
        /// seconds since 1970; the backfill goes oldest first
        public var startedAt: Double
        /// changes when the report would change (e.g. heart rate added later); a sent session with a new fingerprint is sent again
        public var fingerprint: String
        public var isSample: Bool
        public init(id: String, startedAt: Double, fingerprint: String = "", isSample: Bool = false) {
            self.id = id; self.startedAt = startedAt; self.fingerprint = fingerprint; self.isSample = isSample
        }
    }

    /// 15 PUTs a minute at most: under the server's 20 writes/min per IP and 60 PUTs/min per user.
    public static let paceS = 4.0
    public static let retryBaseS = 10.0
    public static let retryCapS = 900.0

    public private(set) var enabled = true
    /// oldest first
    public private(set) var pending: [Session] = []
    /// id -> fingerprint that the server has
    public private(set) var sent: [String: String] = [:]
    /// id -> fingerprint the server refused for good (too large, invalid); retried only when the fingerprint changes
    public private(set) var rejected: [String: String] = [:]
    public private(set) var failures = 0
    /// no request before this time (seconds since 1970): pacing, backoff or Retry-After
    public private(set) var notBefore = 0.0
    public private(set) var lastError: String?
    /// the pod sessions the uploaded style profile was computed from
    public private(set) var styleBasis: String?

    public init() {}

    public var syncedCount: Int { sent.count }

    /// The server's session id rule: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`.
    public static func isValidId(_ id: String) -> Bool {
        let ok = { (c: Character) in c.isASCII && (c.isLetter || c.isNumber) }
        return (1...64).contains(id.count) && ok(id.first!) && id.allSatisfy { ok($0) || ".-_".contains($0) }
    }

    /// Queues every real session the server does not have in this version. Samples never sync; nothing is queued while off.
    public mutating func enqueue(_ sessions: [Session]) {
        guard enabled else { return }
        for s in sessions where !s.isSample && sent[s.id] != s.fingerprint && rejected[s.id] != s.fingerprint {
            if let i = pending.firstIndex(where: { $0.id == s.id }) { pending[i] = s } else { pending.append(s) }
        }
        pending.sort { ($0.startedAt, $0.id) < ($1.startedAt, $1.id) }
    }

    /// The session to send now, or nil (off, empty, or waiting).
    public func next(now: Double) -> String? {
        enabled && now >= notBefore ? pending.first?.id : nil
    }

    public mutating func succeeded(_ id: String, now: Double) {
        if let i = pending.firstIndex(where: { $0.id == id }) { sent[id] = pending.remove(at: i).fingerprint }
        failures = 0
        lastError = nil
        notBefore = now + Self.paceS
    }

    /// `permanent`: the server will never take this version (drop it). Otherwise retry after `retryAfter` (a 429's Retry-After)
    /// or the doubling backoff, capped at `retryCapS`.
    public mutating func failed(_ id: String, now: Double, retryAfter: Double? = nil, permanent: Bool = false, error: String) {
        lastError = error
        if permanent {
            if let i = pending.firstIndex(where: { $0.id == id }) { rejected[id] = pending.remove(at: i).fingerprint }
            notBefore = now + Self.paceS
            return
        }
        failures += 1
        let backoff = min(Self.retryCapS, Self.retryBaseS * pow(2, Double(min(failures - 1, 30))))
        notBefore = now + max(Self.paceS, retryAfter ?? backoff)
    }

    /// The session is gone from the phone (or has no report): stop trying, without an error.
    public mutating func drop(_ id: String) { pending.removeAll { $0.id == id } }

    /// Off clears what is waiting; what was sent stays on the server (and is remembered) until "Delete my web data".
    public mutating func setEnabled(_ on: Bool) {
        enabled = on
        if !on { pending = []; failures = 0; notBefore = 0 }
    }

    /// After `DELETE /api/me/sessions`: sync goes off and nothing counts as sent, so turning it on again uploads everything.
    public mutating func forget() {
        self = WebSyncQueue()
        enabled = false
    }

    public func needsStyle(_ basis: String) -> Bool { enabled && styleBasis != basis }
    public mutating func styleSent(_ basis: String) { styleBasis = basis }
}
