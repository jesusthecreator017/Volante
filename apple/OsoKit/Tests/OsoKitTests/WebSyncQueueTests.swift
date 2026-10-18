import Foundation
import Testing
@testable import OsoKit

private typealias S = WebSyncQueue.Session

private func sessions() -> [S] {
    // newest first, like SessionStore.sessions
    [S(id: "c", startedAt: 300), S(id: "s", startedAt: 250, isSample: true), S(id: "a", startedAt: 100), S(id: "b", startedAt: 200)]
}

/// Drains the queue at `now`, recording every id handed out, sending each successfully.
private func drain(_ q: inout WebSyncQueue, now: inout Double) -> [String] {
    var out: [String] = []
    while let id = q.next(now: now) {
        out.append(id)
        q.succeeded(id, now: now)
        now += WebSyncQueue.paceS
    }
    return out
}

@Test func testBackfillOldestFirst() {
    var q = WebSyncQueue()
    q.enqueue(sessions())
    var now = 0.0
    #expect(drain(&q, now: &now) == ["a", "b", "c"])
    #expect(q.syncedCount == 3 && q.pending.isEmpty)
}

@Test func testQueueResumesWithoutDuplicates() throws {
    var q = WebSyncQueue()
    q.enqueue(sessions())
    let first = q.next(now: 0)!
    q.succeeded(first, now: 0)
    // the app quits (or signs out) mid-backfill: persist, reload, re-scan the same sessions
    var back = try JSONDecoder().decode(WebSyncQueue.self, from: JSONEncoder().encode(q))
    #expect(back == q)
    back.enqueue(sessions())
    back.enqueue(sessions())
    var now = 100.0
    let rest = drain(&back, now: &now)
    #expect(rest == ["b", "c"])
    #expect(!rest.contains(first))
    // nothing new: a later scan queues nothing
    back.enqueue(sessions())
    #expect(back.pending.isEmpty)
    // a session whose content changed (heart rate added) is sent again, once
    back.enqueue([S(id: "a", startedAt: 100, fingerprint: "hr60")])
    #expect(drain(&back, now: &now) == ["a"])
}

@Test func testRetryBackoffCaps() {
    var q = WebSyncQueue()
    q.enqueue([S(id: "a", startedAt: 1)])
    var now = 0.0
    var delays: [Double] = []
    for _ in 0..<12 {
        let id = q.next(now: now)!
        q.failed(id, now: now, error: "offline")
        #expect(q.next(now: now) == nil)  // not before the backoff ends
        delays.append(q.notBefore - now)
        now = q.notBefore
    }
    #expect(delays.first == WebSyncQueue.retryBaseS)
    #expect(zip(delays, delays.dropFirst()).allSatisfy { $0 <= $1 })
    #expect(delays.last == WebSyncQueue.retryCapS)
    #expect(q.lastError == "offline")
    #expect(q.pending.map(\.id) == ["a"])  // still queued
    // Retry-After wins over the backoff; success resets it
    let id = q.next(now: now)!
    q.failed(id, now: now, retryAfter: 42, error: "rate limited")
    #expect(q.notBefore == now + 42)
    now += 42
    q.succeeded(q.next(now: now)!, now: now)
    #expect(q.lastError == nil && q.failures == 0)
}

@Test func testPacing() {
    var q = WebSyncQueue()
    q.enqueue(sessions())
    let id = q.next(now: 10)!
    q.succeeded(id, now: 10)
    #expect(q.next(now: 10) == nil)
    #expect(q.next(now: 10 + WebSyncQueue.paceS) != nil)
    #expect(60 / WebSyncQueue.paceS <= 15)
}

@Test func testPermanentFailureDropsUntilChanged() {
    var q = WebSyncQueue()
    q.enqueue([S(id: "a", startedAt: 1)])
    q.failed("a", now: 0, permanent: true, error: "too large")
    #expect(q.pending.isEmpty && q.syncedCount == 0 && q.lastError == "too large")
    q.enqueue([S(id: "a", startedAt: 1)])
    #expect(q.pending.isEmpty)
    q.enqueue([S(id: "a", startedAt: 1, fingerprint: "new")])
    #expect(q.pending.map(\.id) == ["a"])
}

@Test func testSampleSessionsNeverQueued() {
    var q = WebSyncQueue()
    q.enqueue(sessions())
    q.enqueue([S(id: "s", startedAt: 250, fingerprint: "hr1", isSample: true)])
    #expect(!q.pending.contains { $0.id == "s" })
}

@Test func testDisableClearsPending() {
    var q = WebSyncQueue()
    q.enqueue(sessions())
    q.setEnabled(false)
    #expect(q.pending.isEmpty && q.next(now: 1e9) == nil)
    q.enqueue(sessions())  // ignored while off
    #expect(q.pending.isEmpty)
    q.setEnabled(true)
    q.enqueue(sessions())
    #expect(q.pending.map(\.id) == ["a", "b", "c"])
}

@Test func testForgetAfterDeletingWebData() {
    var q = WebSyncQueue()
    q.enqueue(sessions())
    var now = 0.0
    _ = drain(&q, now: &now)
    q.styleSent("a,b,c")
    q.forget()
    #expect(!q.enabled && q.syncedCount == 0 && q.pending.isEmpty && q.needsStyle("a,b,c") == false)
    q.setEnabled(true)
    #expect(q.needsStyle("a,b,c"))
    q.enqueue(sessions())
    #expect(q.pending.count == 3)
}

@Test func testSessionIdRule() {
    #expect(WebSyncQueue.isValidId("20261018-120000-ab12"))
    #expect(!WebSyncQueue.isValidId(".hidden"))
    #expect(!WebSyncQueue.isValidId(""))
    #expect(!WebSyncQueue.isValidId("Jesus's iPhone"))
    #expect(!WebSyncQueue.isValidId(String(repeating: "a", count: 65)))
    #expect(WebSyncQueue.isValidId(String(repeating: "a", count: 64)))
}

@Test func testUploadBodyIsCompactReport() throws {
    let cap = try ImuFile.parse([UInt8](try Data(contentsOf: URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .appendingPathComponent("../../../../arena/kit/tests/data/twins_vector.imu.bin").standardized)))
    let r = SessionReporter.build(parts: [cap.samples], startedAt: "2026-10-17T18:00:00Z", mount: .identity)
    let body = r.uploadBody()
    let top = try #require(try JSONSerialization.jsonObject(with: body) as? [String: Any])
    #expect(Array(top.keys) == ["report"])
    let pretty = try JSONSerialization.jsonObject(with: Data(r.json().utf8)) as! NSDictionary
    #expect((top["report"] as! NSDictionary) == pretty)
    #expect(body.count < r.json().utf8.count)
    #expect(!String(decoding: body, as: UTF8.self).contains("\n"))
}
