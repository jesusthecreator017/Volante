# Web dashboard for app users: design

Date: 2026-10-18. Status: design agreed in conversation; written spec pending review.

## 1. Goal
People who use the Volante iPhone app can see their training stats on the website. The web is mainly the Arena, so this is a quiet,
private section: **Account → Dashboard**. It is shown only when the account is linked to the mobile app.

### Owner decisions
- **Data:** the app sends **session summaries only**, the `volante-report/1` report it already computes per session. No raw motion
  data (`imu.bin`) leaves the phone; this matches the Twins privacy rule.
- **Sync:** **automatic** when signed in to the Arena in the app: after each saved session, plus a one-time backfill. A Settings toggle
  turns web sync off, and "Delete my web data" removes everything from the server.
- **Dashboard content:** overview and trends, the session list and session detail, strokes and style.
- **Visibility:** only the signed-in owner. Nothing is public.
- **Placement:** no new top-level tab. **Account menu → Dashboard**, shown only when the account is mobile-linked.
- **"Mobile-linked"** means this account has signed in from the iPhone app at least once. The server records it at the app sign-in
  exchange.

### Non-goals
- No raw data upload or re-analysis on the server.
- No public profiles.
- No editing sessions on the web.
- No Android/Capacitor app sync (the older `app/` client) in this phase.

## 2. Data on the server (`arena/server`)
- `users` gains `app_linked_at TEXT NULL`. It is set at `POST /api/app/session` success on the first time and kept afterwards.
- New table `app_sessions`:
  - `(user_id INTEGER NOT NULL REFERENCES users(id), id TEXT NOT NULL, started_at TEXT NOT NULL, source TEXT NOT NULL, report TEXT NOT NULL,`
    `received_at TEXT NOT NULL, PRIMARY KEY (user_id, id))`
  - `id`: the app's session id, opaque, 1-64 chars `[A-Za-z0-9._-]`, with no device or personal data.
  - `report`: the validated JSON text.
- New table `app_style (user_id INTEGER PRIMARY KEY, profile TEXT NOT NULL, received_at TEXT NOT NULL)`. It holds the latest
  `volante-style/1` profile the app computed, if the app has one.
- Account deletion and "Delete my web data" remove both tables' rows. Account deletion also clears `app_linked_at`. Backups follow
  the existing nightly backup.
- **Validation** (server side, same rules as `docs/strokes/report-format.md` and `report.schema.json`):
  - `schema == "volante-report/1"`, keys and types per the schema, numbers finite and in sane ranges, the nullability rules.
  - Size ≤ 16 KB.
  - The style profile through the kit's `oso_arena.style.parse_profile` / `validate_profile`.
  - Reject with `400 {"error": "invalid_report", "detail": ...}`.
  - No new Python dependency: a hand-written validator mirroring the schema, tested against the repo's examples and parity vector.

## 3. API (additive, all `[auth]`, `cache-control: no-store`; writes need CSRF like every other write)
| Method and path | Purpose |
|---|---|
| `GET /api/me` | gains `app_linked: bool` |
| `PUT /api/me/sessions/{id}` | body `{"report": <volante-report/1>}` → `200 {"id", "started_at"}` (idempotent upsert) |
| `GET /api/me/sessions?before=&limit=` | newest first, `{"items": [SessionSummary], "next_before"}`. `SessionSummary` = id, started_at, source, duration_s, swings, p90_kmh, rallies, heart avg |
| `GET /api/me/sessions/{id}` | `{"id", "report"}`, 404 if not the owner's |
| `DELETE /api/me/sessions/{id}` | deletes one session |
| `DELETE /api/me/sessions` | deletes all of the user's sessions and the style profile ("Delete my web data") |
| `PUT /api/me/style` | body = a style profile → 200 |
| `GET /api/me/dashboard` | aggregates computed on the server from the stored reports (below) |

`GET /api/me/dashboard` returns:
- `totals`: sessions, swings, active minutes, rallies.
- `weekly`: the last 12 ISO weeks, each with sessions, swings and active minutes.
- `trends`: per session, in time order (last 50): `p90_kmh`, `swings_per_min`, `median_gap_s`, `hr_avg`.
- `bests`: fastest swing (`max_kmh`), most swings in a session, longest rally, highest swings per minute. Each best carries its session
  id and date.
- `strokes`: stroke-group shares over the last 10 sessions that have strokes, plus the overall mix, with `experimental: true`.
- `style`: the latest profile (or null), plus the user's twin model id if one exists.

Pod (racquet) and watch (wrist) speeds are never mixed. Aggregates of speeds use pod sessions only, and watch sessions show "hand speed"
separately, or are excluded from speed stats.

**Limits:** at most 5,000 sessions per user, PUT rate-limited to 60 per minute per user, and a 16 KB body cap.

## 4. iPhone app (`apple/`)
- **`WebSync`** (OsoPhone, using `ArenaClient`):
  - When signed in to the Arena and sync is on: after a session's report is computed or saved, `PUT` it, with a retry queue on failure.
  - A one-time backfill of existing sessions, oldest first, rate-limited.
  - `PUT /api/me/style` when the style profile changes.
  - A sample session never syncs.
- **Settings:** "Show my sessions on volante web" (default on after Arena sign-in) and "Delete my web data", which calls
  `DELETE /api/me/sessions` after a confirmation.
- **Status:** a small line, "Synced to the web · 12 sessions" or the last error.
- Session ids sent are the app's own ids, with no device name.

## 5. Website (`site/`)
- **Account menu** (avatar): Dashboard (only when `me.app_linked`), Settings, Sign out.
- **Routes:**
  - `/account/dashboard`: the overview.
  - `/account/dashboard/sessions/:id`: one session.
  - Both are signed-in only.
  - Not linked: an explainer, "Sign in to the Arena in the Volante app to see your stats here".
- **Overview:**
  - Totals.
  - A 12-week volume bar chart.
  - Trend lines: typical/hard speed "estimated", swings/min, rhythm, heart rate.
  - Bests, linking to their sessions.
  - Strokes: a group-mix bar labelled experimental.
  - Style: profile numbers and "Your twin" link if one exists.
  - The recent sessions list with pagination.
- **Session page:** headline numbers, intensity timeline (swings/min and p90 per minute, HR overlay), stroke groups, heart-rate
  summary and insights. Speeds are always labelled "estimated" and strokes "experimental". Watch sessions are labelled "wrist".
- **Rendering:**
  - Charts are inline SVG built with the `h()` helper. No chart library, no new runtime dependency, CSP unchanged.
  - Mock data in `src/mock.ts` for `dev:mock` (linked and unlinked users, sessions with and without strokes or HR).
- **Privacy page:** what is stored (summaries only), who sees it (only you), and how to delete it (app setting, account deletion).

## 6. Docs and contracts
- `arena/handoff/PROMPT.md`: the new endpoints and types (the kit's handoff test requires every `site/src/types.ts` field there).
- `site/README.md` "For the Pi agent": the routes.
- `docs/strokes/report-format.md`: note that reports are stored server-side for the owner's dashboard.
- Deploy: the server change ships through CD. The DB migration is additive (`ALTER TABLE` / `CREATE TABLE IF NOT EXISTS`).

## 7. Testing
- **Server:**
  - Validator: the repo's examples and parity vector pass; malformed, oversized and NaN inputs are rejected.
  - Auth, CSRF and ownership: another user's session gives 404.
  - Upsert idempotency and delete-all.
  - Dashboard aggregates on fixtures, including pod/watch separation and empty states.
  - `app_linked` set by the app exchange.
- **App:** `WebSync` queue, retry and backfill logic in OsoKit (pure, tested); request bodies match the server contract.
- **Site:**
  - Router: the menu entry is only shown when linked.
  - The dashboard renders mock data and empty states, with text-node safety.
  - Screenshots at 390 and 1280 px, light and dark.
- **End-to-end on the Pi after deploy**, with the owner's GitHub account: sign in from the app, sync, then see the dashboard.

## 8. Order
1. Server.
2. Site, against the mock data.
3. App sync.
4. Deploy through CD, then the owner tests with their account.
