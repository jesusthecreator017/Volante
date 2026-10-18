# Handoff: Volante (oso) — state as of 2026-10-18 01:30 EDT

For the next agent (a cloud session or a fresh local one). Read this first, then `README.md`, `apple/README.md`,
`arena/server/README.md`, `deploy/pi/README.md`. Everything here was true when written; verify before acting.

**Owner rules (from the user, Alvaro, GitHub `a1vcm`):**
- Work autonomously: pick the next thing, don't ask permission for normal work. **Auto-merge your PRs** once CI passes
  (`gh pr merge --merge`; `--auto` is not enabled on the repo).
- **No Claude attribution**: no `Co-Authored-By` lines, no "Generated with Claude Code" in commits or PR bodies.
- One branch + PR per task (`apple/…`, `site/…`, `arena/…`, `tools/…`, `feature/…`), never commit to `main`; merge `origin/main` first.
- Never commit session data, video, names of participants, or secrets. Raw data lives under `~/Datasets` on the Mac.
- Teammate **Jesus** (`jesusthecreator017`, repo owner) also merges and pushes; check `git log origin/main` for his work.

## 1. What exists

| part | where | status |
|---|---|---|
| Racquet pod firmware (XIAO ESP32-S3 + MPU-9250, Rust/Embassy) | `firmware/`, `proto/`, BLE contract `docs/ble-profile.md` | working; pod BLE name `OSO-4C99` |
| 3D replay / Arena viewer (Rust/wgpu/wasm) | `crates/`, `web/arena/` | working |
| Website (TS + Vite, no innerHTML, strict CSP) | `site/` | live at `https://pi5server-1.tail05420e.ts.net` (Funnel) |
| Arena server (FastAPI, sandboxed model checks/matches) | `arena/server/` | live on the Pi; CD via `.github/workflows/deploy.yml` (self-hosted runner on the Pi, deploys on push to main) |
| Arena kit (Python, PPO trainer, House Base model) | `arena/kit/` (`oso-arena`), contract `arena/contract/` | v0.4.0 |
| LAN "server B" (sessions, replays, Fitbit proxy; no auth) | `app/server/server.mjs` on the Pi | `http://…:8090`, tailnet HTTPS `https://pi5server-1.tail05420e.ts.net:8444` (tailscale serve, **tailnet only**) |
| iPhone + Apple Watch apps (SwiftUI, XcodeGen) | `apple/` (`OsoKit` package, `OsoPhone`, `OsoWatch`, `OsoLive` widget/Live Activity) | iPhone app installed on Alvaro's and Jesus's phones; **watch app never installed on the real watch** |
| Capacitor web app (older) | `app/` | maintained, display name Volante |
| Stroke ML (Jesus) | `tools/strokes/`, `models/strokes/`, `docs/strokes/` | 4-group model is the recommended v0; 11-class model experimental (model card) |
| Twin automation | `tools/twin/feed_sessions.py` | launchd agent on Alvaro's Mac every 20 min |

## 2. iPhone app (`apple/`)

Build: `cd apple && xcodegen && xcodebuild -project Oso.xcodeproj -scheme OsoPhone -destination 'generic/platform=iOS' -allowProvisioningUpdates build`.
Signing per developer in `apple/Signing.local.xcconfig` (gitignored): team `27H3PTGFC3` (free/personal), `OSO_BUNDLE_PREFIX = com.alvarocm.oso`,
plus `GOOGLE_CLIENT_ID = 135157947015-6o41acut3lvic4h85gsjkeas6gkvdmbe.apps.googleusercontent.com` and the reversed
`GOOGLE_REDIRECT_SCHEME` (an iOS OAuth client ID is not a secret). A cloud session cannot sign or install: it can build for
the simulator/generic and run `swift test` in `apple/OsoKit` (51 tests).
Devices (local only): Alvaro's iPhone `00008140-000C41AC1862201C`, Jesus's iPhone `00008140-0001295201BB001C` (registered in the profile;
free-team installs expire after 7 days), Alvaro's watch `00008310-000D46D23487601E` (paired to his iPhone, **not** registered).
iOS deployment target 26.1; phones run iOS 27.

Tabs: **Summary** (customizable widget dashboard: Edit reorders/removes/resizes/adds; layout JSON in `@AppStorage("volante.summary.layout")`),
**Play** (pod), **Sessions**, **Arena**, **Devices** (+ Settings sheet).
Recent features (all merged): Liquid Glass redesign; Volante palette from the site (`Theme.swift`); weekly goal ring + streaks + home-screen widget (App Group);
Twin screen (Summary → Your style: style profile on the phone, byte-identical to `oso-rec profile`, exports `my_style.json`);
Arena tab (ladder Open/Twins, model, match + 3D replay via the site viewer in a WKWebView, tournaments, Train & upload, **GitHub sign-in** via
ASWebAuthenticationSession + `/api/app/session` PKCE exchange, session id in the Keychain);
post-session Summary (`SessionReport` + `InsightRules` in OsoKit, report contract `docs/strokes/report-format.md`);
**health**: `OsoKit/HeartStats` (avg/max, 5 zones of max HR, effort = Edwards TRIMP, 1-min recovery), session Heart rate card, **Add heart rate**
(Fitbit / Apple Health via HealthKit / CSV-JSON file), Settings age + max HR; all speeds shown in **km/h**; swings list collapsible (first 10).
Not verified on screen yet: the health dashboard (#73) was installed on Alvaro's phone but nobody has reported on it.

## 3. The Pi (`pi5server-1`)

Owned by Jesus's tailnet `tail05420e.ts.net` (`reychucho859`), shared to Alvaro (`a1vcm`). SSH: `ssh itsfrosty@pi5server-1.tail05420e.ts.net`
(Tailscale SSH; `itsfrosty` is the Tailscale operator, no sudo). Root actions need the user to run `su - jesus_g7 -c "sudo …"` with a password
(never ask for it, never type it). Time zone America/New_York.
- **Arena**: system services `oso-arena-web`, `oso-arena-supervisor@1/2`, `oso-arena-scheduler`, backup timer; code `/opt/oso-arena`,
  data `/var/lib/oso-arena`, secrets `/etc/oso-arena/arena.env` (GitHub OAuth app client ID `Ov23lidbLjFKu2igszaw` + secret, session secret,
  `OSO_ARENA_BASE_MODEL=/opt/oso-arena/base_40min.safetensors`). Funnel `https://pi5server-1.tail05420e.ts.net` → 127.0.0.1:8700; health 8701.
  Sign-in works (fixed: the secret had been pasted twice). Ladder: House Bot, House Base, Alvaro's "Test Net Dominator". Twins ladder empty.
- **Server B** (user unit `oso-replay`, `~/oso-b/app/server`, data `~/oso-b/data`): Fitbit token `~/oso-b/data/fitbit.json` (mode 600) is
  **Jesus's** (connected 2026-10-18 00:41). `GET /api/fitbit/heart-rate?from=&to=` works (36k samples/24 h). Never Funnel 8090/8080/8443/8444.
- Diagnostic helper scripts in `~itsfrosty/` (`arena-auth-diag.sh`, `arena-status.py`, `arena-github-oauth.sh`, `arena-sandbox-test.sh`).

## 4. Data and the twin (Alvaro's Mac only)

- Sessions from Alvaro's phone: `~/Datasets/volante-phone/all/<id>/{imu.bin,meta.json}` — 5 pod sessions on 2026-10-17 (651 swings, 26.6 min).
- Jesus's Fitbit heart rate for that evening: `~/Datasets/volante-hr/jesus_hr_5h.json`; the 5 sessions with his HR attached were copied into
  Volante on **Jesus's** phone (not yet on Alvaro's).
- Style profile `~/Datasets/volante-twin/my_style.json`: p50 15.2 / p90 44.8 km/h, cv 0.45, median gap 1.67 s → build 3/7/10/4.
- Training (`oso-arena train --style`): run1 (60 min from House Base), run2 (30 min from run1, 5 sessions), run3 (30 min from run2,
  `--style-weight 0.1`, was at 22/30 min at handoff). **`twin_run2.safetensors` passes the Arena-style pooled fidelity** (20 matches vs bot +
  House Base: tempo ratio 1.04, build ok); 72 % vs House Base, 100 % vs bot. Not uploaded yet. Evaluate run3 the same way
  (pooled fidelity script pattern: `match.play` alternating opponents/sides → `style.fidelity`).
- `tools/twin/feed_sessions.py` (launchd `com.volante.twin-feed`, every 20 min): pulls new sessions from Alvaro's phone via `devicectl`,
  re-profiles, trains the next run from the latest same-build checkpoint; state `~/Datasets/volante-twin/state.json`, log `feed.log`.
  **Standing user wish: every new session is fed into the twin.**

## 5. Open items, in priority order

1. **Apple Watch** (user is actively trying): the watch app's profile lists only the iPhone; a free team registers a device only when Xcode
   talks to it. Campus Wi-Fi (eduroam/UFGetOnline/UFGuest) isolates devices; a VPN doesn't help. Plan given to the user: Mac + iPhone + watch
   on **Jesus's hotspot** (Maximize Compatibility), iPhone USB + unlocked, **iPhone Bluetooth off** so the watch uses Wi-Fi, watch awake;
   then `dns-sd -B _remotepairing._tcp` to see the watch, `xcrun devicectl manage pair --device <id>`, accept the code/Trust, Developer Mode
   on the watch, then build with `-destination id=00008310-000D46D23487601E -allowProvisioningDeviceRegistration` and install (or
   iPhone Watch app → Available Apps). Last attempt stalled at "Waiting to pair".
2. Upload the twin (run2 or a better run3) via Arena → Train & upload; it needs 10 matches before the Twins check.
3. Verify the health dashboard on a phone (screenshots impossible from the Mac without a sudo tunnel; ask the user).
4. Add Jesus's HR to the sessions on Alvaro's phone (devicectl copy to the app container, see §4) once his phone is connected.
5. Website: a session-report page rendering `volante-report/1` (contract ready, `docs/strokes/report.example*.json`); pods pairing
   (`POST /api/me/pods`) is designed as future work.
6. Session-summary brief leftovers: none blocking; LLM wording and weekly notifications defaulted to off.

## 6. Gotchas learned the hard way
- zsh: unquoted `$var` doesn't word-split; use arrays or bash.
- iPhone simulator runtime 27.1 refuses iPhone devices on this Mac; only device builds or `generic/platform=iOS` compile checks.
- Plain `http://` from the app on cellular goes through **iCloud Private Relay** and fails: the app uses the tailnet HTTPS 8444 and falls back to
  the Pi's tailnet IP `100.103.93.45:8090`. Phones must be on the Pi's tailnet (Jesus's phone had been on another account `tail4766d9`).
- `gh pr checks --watch` then `gh pr merge --merge`; pushing to main triggers the Pi deploy (check `gh run list --workflow deploy.yml`).
- A root-run script should live where only root can write it; the ones in `~itsfrosty` were one-offs.
