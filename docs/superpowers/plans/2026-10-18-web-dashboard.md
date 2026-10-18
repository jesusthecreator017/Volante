# Web Dashboard for App Users Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Signed-in iPhone app users get a private Account → Dashboard on the website, fed by session summaries the app syncs to the Arena server.

**Architecture:**
- The app PUTs each session's `volante-report/1` JSON (and its style profile) to new `/api/me/...` endpoints on the Arena server (`arena/server`, FastAPI + SQLite).
- The server validates and stores the data, then computes dashboard aggregates.
- The site (`site/`, TS + Vite, `h()` DOM helper, no framework) renders `/account/dashboard` and `/account/dashboard/sessions/:id` with inline-SVG charts.
- The account menu shows Dashboard only when `/api/me` says `app_linked`.

**Tech Stack:** Python 3.11 FastAPI + sqlite3 (server); TypeScript + Vite + vitest + happy-dom (site); Swift/SwiftUI + OsoKit SPM (app).

**Spec:** `docs/superpowers/specs/2026-10-18-web-dashboard-design.md`

## Global Constraints
- **Data:** session summaries only, as `volante-report/1` (`docs/strokes/report-format.md`, `docs/strokes/report.schema.json`). No raw IMU data.
- **Privacy:** private to the owner. Every endpoint is `[auth]`, writes need CSRF (`require_user(request)`), reads use `require_user(request, csrf=False)`, and all responses are `cache-control: no-store`.
- **Limits:** report body ≤ 16 KB, ≤ 5,000 sessions per user, PUT ≤ 60 per minute per user. Session id matches `^[A-Za-z0-9._-]{1,64}$`.
- **Speeds:** pod (racquet) and watch (wrist) speeds are never mixed. Speed aggregates use `source == "pod"` only.
- **Copy:** speeds say "estimated", strokes say "experimental", watch sessions are labelled "wrist".
- **No new dependencies** in server, site or app. Site: CSP unchanged, no inline style or script, user text as text nodes.
- **Migrations are additive:** `CREATE TABLE IF NOT EXISTS` and the existing `db.migrate` column mechanism.
- **Placement:** Account menu → Dashboard (only if `me.app_linked`). No new top-level nav tab.
- **Commits:** every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `feature/web-dashboard`, no push until the owner's PR.
- **Python:** `/Users/jesusgonzalez07/Desktop/MacToUSB/Rust/oso/tools/.venv/bin/python`. Server tests follow `arena/server/tests/test_pods.py` (read it for the app/test-client setup).

## Review Focus
1. **Another user's session id:** GET, PUT or DELETE must 404 or affect only the caller's own rows, never leak or overwrite. Pinned in Task 2 `test_sessions_are_private_to_the_owner`.
2. **A report with NaN, Infinity, a huge number or a 100 KB string:** rejected with 400 `invalid_report`, nothing stored. Pinned in Task 1 `test_validator_rejects_malformed`.
3. **Unlinked account visiting `/account/dashboard` directly:** shows the explainer, makes no failing data calls, and has no menu entry. Pinned in Task 4 `test_dashboard_unlinked_explainer`.
4. **Watch-only user:** the dashboard shows hand-speed sessions labelled wrist and no racquet speed stats, with no division by zero. Pinned in Task 2 `test_dashboard_watch_only`.
5. **App offline or signed out mid-backfill:** the queue persists and resumes without duplicates, because PUT is idempotent by id. Pinned in Task 6 `testQueueResumesWithoutDuplicates`.

---

### Task 1: Server storage and report validator

**Files:**
- Modify: `arena/server/osoarena/db.py`: add to `SCHEMA` the tables `app_sessions` and `app_style` (spec §2), and the `users.app_linked_at TEXT` column through `migrate`.
- Create: `arena/server/osoarena/reports.py`: `validate_report(obj) -> dict` (raises `ValueError(msg)`) and `summary_of(report) -> dict`.
- Test: `arena/server/tests/test_reports.py`.

**Interfaces:**
- Produces:
  - `reports.validate_report(obj: object) -> dict` returns the same dict, checked. It implements every rule of `report-format.md` §1-3 by hand: exact keys and types, finite numbers, `schema == "volante-report/1"`, `source in {"pod","watch"}`, nullability (§2), strokes/model both null or both present, insights ≤ 4 with severity in {info, tip, praise}, timeline ≤ 600 entries.
  - `reports.MAX_BYTES = 16384`.
  - `reports.summary_of(r) -> {"started_at","source","duration_s","active_s","swings","p90_kmh","max_kmh","rallies","longest_rally","hr_avg","has_strokes"}`.

- [ ] **Step 1: Failing tests.**
  - `test_examples_validate`: `docs/strokes/report.example.json`, `report.example-strokes.json` and the parity vector's expected report (find it via `report-format.md` §5) all pass.
  - `test_validator_rejects_malformed`: NaN, Infinity, a negative count, a wrong schema, an extra top-level key, `strokes` without `model`, a 100 KB `insights[0].body`, and a non-dict are each rejected.
  - `test_migrate_adds_tables_and_column`: on a fresh in-memory DB the tables and the `app_linked_at` column exist, and running `migrate` again is a no-op.
- [ ] **Step 2:** Run them and see them fail (ImportError or missing table).
- [ ] **Step 3:** Implement. Use plain Python checks and one helper per section; keep it under ~200 lines.
- [ ] **Step 4:** Run the server suite (`cd arena/server && python -m pytest -q` or the runner `test_pods.py` uses), all green.
- [ ] **Step 5:** Commit `feat(server): app session storage + volante-report/1 validator (dashboard task 1)`.

### Task 2: Server API and dashboard aggregates

**Files:**
- Modify: `arena/server/osoarena/web.py`:
  - the endpoints of spec §3
  - `GET /api/me` gains `app_linked` (true iff `users.app_linked_at` is set)
  - `POST /api/app/session` sets `app_linked_at` the first time
  - account deletion removes `app_sessions`/`app_style` and clears `app_linked_at`
  - an in-memory per-user rate limit (60 PUTs per minute), using the existing rate-limit helpers around line 64.
- Create: `arena/server/osoarena/dashboard.py`: `aggregate(rows: list[dict], style: dict|None, twin_model_id: int|None, today: date) -> dict`, the shape of spec §3. It is a pure function over the summaries plus the full reports when strokes are needed.
- Test: `arena/server/tests/test_app_sessions.py`, `arena/server/tests/test_dashboard.py`.

**Interfaces:**
- Consumes: Task 1 `validate_report`, `summary_of`, `MAX_BYTES`.
- Produces: JSON shapes used by Task 3. Copy them verbatim into the docstring of each handler.
  - `SessionSummary = {id, started_at, source, duration_s, active_s, swings, p90_kmh, max_kmh, rallies, longest_rally, hr_avg, has_strokes}`
  - `Dashboard = {totals:{sessions, swings, active_min, rallies}, weekly:[{week:"YYYY-Www", sessions, swings, active_min}] (12, oldest first), trends:[{id, started_at, p90_kmh, swings_per_min, median_gap_s, hr_avg}] (pod sessions, last 50, oldest first), wrist:{sessions, swings}, bests:{max_kmh, most_swings, longest_rally, best_rate} each {value, session_id, started_at} | null, strokes:{groups:{power,mid,touch,not_a_hit,unclassified} shares over last 10 sessions with strokes, sessions:int, experimental:true} | null, style: StyleProfile | null, twin_model_id: int | null}`

- [ ] **Step 1: Failing tests.**
  - `test_put_get_list_delete_roundtrip`
  - `test_put_is_idempotent_by_id`
  - `test_put_rejects_invalid_and_oversize` (400 and 413 or 400)
  - `test_bad_session_id_400`
  - `test_sessions_are_private_to_the_owner` (user B GET, PUT or DELETE of user A's id → 404, A's row unchanged)
  - `test_writes_need_csrf`
  - `test_delete_all_removes_sessions_and_style`
  - `test_app_exchange_sets_app_linked` (`/api/me` false before, true after; use the existing app-code test helper in `test_signin.py`)
  - `test_style_put_validates` (bad profile → 400)
  - `test_rate_limit_put` (61st PUT in a minute → 429)
  - `test_dashboard_aggregates` (fixture of 5 pod + 2 watch reports; exact totals, weeks, bests, pod-only trends)
  - `test_dashboard_watch_only` (no speed trends or bests for speed, no exception)
  - `test_dashboard_empty`
- [ ] **Step 2:** Run them and see them fail.
- [ ] **Step 3:** Implement. Pagination: `before` is an ISO `started_at` cursor, and `limit` defaults to 20 with a maximum of 100.
- [ ] **Step 4:** Full server suite green.
- [ ] **Step 5:** Commit `feat(server): /api/me/sessions, /api/me/style, /api/me/dashboard, app_linked (dashboard task 2)`.

### Task 3: Site data layer (types, API, mock)

**Files:**
- Modify:
  - `site/src/types.ts`: `Me.app_linked?: boolean`, `SessionSummary`, `SessionPage`, `Dashboard`, `SessionReport` (`volante-report/1`, matching `report-format.md`)
  - `site/src/api.ts`: `api.mySessions(before?)`, `api.mySession(id)`, `api.myDashboard()`
  - `site/src/mock.ts`: a linked user with about 30 pod sessions over 10 weeks (some with strokes or HR), 3 watch sessions, and an unlinked user switchable via the existing mock sign-in path. Follow how the mock handles `/api/me` today.
- Test: `site/test/dashboard-api.test.ts`.

**Interfaces:**
- Produces: the TS types mirroring Task 2's shapes exactly.

- [ ] **Step 1:** Write a failing vitest. The mock returns a well-formed `Dashboard`, `mySessions` paginates, and `mySession` returns a report whose `schema` is `volante-report/1`.
- [ ] **Step 2:** Run (`cd site && npm test`) and see it fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `npm test && npm run typecheck`, green.
- [ ] **Step 5:** Commit `feat(site): dashboard types, API client, mock data (dashboard task 3)`.

### Task 4: Site pages, account menu and charts

**Files:**
- Create:
  - `site/src/pages/dashboard.ts`: the overview and the session page
  - `site/src/charts.ts`: small SVG chart builders using `h()` (bars, lines, multiple lines with a missing-value gap, sparkline), each with `role="img"` plus an `aria-label` summary.
- Modify:
  - `site/src/routes.ts`: `/account/dashboard`, `/account/dashboard/sessions/:id`
  - the header/account menu component (find where Settings/Sign out live): add Dashboard only if `me.app_linked`
  - `site/src/style.css`: dashboard layout with existing tokens only
  - `site/src/pages/content.ts`: privacy copy (spec §5).
- Test: `site/test/dashboard.test.ts`.

**Interfaces:**
- Consumes: Task 3 API and types.

- [ ] **Step 1: Failing tests.**
  - `test_menu_shows_dashboard_only_when_linked`
  - `test_dashboard_unlinked_explainer` (no data calls)
  - `test_dashboard_renders_overview` (totals, 12 bars, bests linking to sessions, "estimated", "experimental")
  - `test_session_page_renders_report` (timeline, strokes and HR present or absent)
  - `test_wrist_sessions_labelled`
  - `test_text_nodes_only` (an insight title containing `<img src=x onerror=...>` renders as text)
  - `test_signed_out_redirects_to_sign_in` (follow the existing signed-in-only page pattern, e.g. Settings).
- [ ] **Step 2:** Run them and see them fail.
- [ ] **Step 3:** Implement: mobile-first, 390-1280 px, light and dark, accessible charts, no new deps.
- [ ] **Step 4:** `npm test && npm run typecheck && npm run build`, green. Take headless screenshots at 390 and 1280, light and dark, of both pages and the unlinked state with `npm run dev:mock` (port ≠ 5173), and read them.
- [ ] **Step 5:** Commit `feat(site): Account → Dashboard (overview, session page, SVG charts) (dashboard task 4)`.

### Task 5: Contracts and docs

**Files:**
- Modify:
  - `arena/handoff/PROMPT.md`: the endpoints and the types block. `arena/kit/tests/test_handoff_hostile.py::test_prompt_names_every_type_field` must pass.
  - `site/README.md`: routes and "For the Pi agent".
  - `docs/strokes/report-format.md`: one paragraph saying reports are stored for the owner's dashboard.
- Test: `PYTHONPATH=arena/kit:arena/kit/tests python -c "import test_handoff_hostile as t; t.test_prompt_names_every_type_field()"`.

- [ ] **Step 1:** Run the handoff test now. It fails, because the new site types aren't in PROMPT.
- [ ] **Step 2:** Update the docs.
- [ ] **Step 3:** The handoff test passes.
- [ ] **Step 4:** Commit `docs: dashboard API and types in the handoff PROMPT and site README (dashboard task 5)`.

### Task 6: iPhone app web sync

**Files:**
- Create:
  - `apple/OsoKit/Sources/OsoKit/WebSyncQueue.swift`: pure queue logic. It holds pending session ids, ordering (backfill oldest first), retry with backoff, dedupe, and a persisted Codable state.
  - `apple/OsoPhone/WebSync.swift`: wiring. It uses `ArenaClient` (cookie + `x-csrf-token`), observes `SessionStore` saves and report computation, PUTs `/api/me/sessions/{id}` with `{"report": SessionReport}`, PUTs `/api/me/style`, and skips sample sessions.
- Modify:
  - `apple/OsoPhone/SettingsView.swift` (or the Arena settings section): toggle "Show my sessions on volante web" (default on once signed in) and "Delete my web data" (confirmation, then `DELETE /api/me/sessions`)
  - a status line ("Synced · N sessions" or the last error).
- Test: `apple/OsoKit/Tests/OsoKitTests/WebSyncQueueTests.swift`.

**Interfaces:**
- Consumes: Task 2 endpoints; `SessionReport` encoding must produce `volante-report/1` JSON exactly (it already has a parity test; reuse its encoder).

- [ ] **Step 1: Failing tests.**
  - `testBackfillOldestFirst`
  - `testQueueResumesWithoutDuplicates` (persist, reload, already-sent ids not re-sent)
  - `testRetryBackoffCaps`
  - `testSampleSessionsNeverQueued`
  - `testDisableClearsPending`
- [ ] **Step 2:** `cd apple/OsoKit && swift test` and see them fail.
- [ ] **Step 3:** Implement. App wiring builds for the iOS simulator; the build needs watchOS absent locally, so mirror the throwaway-spec approach used before and let CI's Apple job do the full build.
- [ ] **Step 4:** `swift test` green; the iOS app build passes.
- [ ] **Step 5:** Commit `feat(apple): sync session summaries to the web dashboard (dashboard task 6)`.

### Task 7: Deploy and end-to-end check (controller + owner)
- [ ] Open the PR. After the owner merges, CD deploys the server and site, and the migration runs at web start.
- [ ] On the Pi, check `/api/me` shows `app_linked`. The owner signs in from the app, waits for sync, and opens Account → Dashboard on the site with their GitHub account.
