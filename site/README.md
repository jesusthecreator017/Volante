# Volante website (frontend)

Volante is the product name; command and package names keep the `oso` prefix.

The site is the **racquet pod's** landing page (`/`: hero, features with honest status chips, how it works, an example
report, "Watch AI matches in 3D" poster that loads the real viewer on tap, an Arena teaser), Settings (`/settings`: pod display names, delete account), Terms and Privacy. The **Arena** (AI
badminton) is a sub-section under `/arena`: Home, Ladder, Model, Match (3D viewer + rally list; the model page also embeds the model's latest finished match), Tournaments, My models
(upload), Get started. Built against the API contract in `oso-web-app-PROMPT.md` section 6. The Pi agent's
server serves the **API** and this **static build**; it does not render pages.

- TypeScript + Vite, no UI framework, no runtime dependencies besides the bundled font (~17 KB gzipped JS, ~6 KB CSS, plus ~45 KB of self-hosted Barlow Condensed woff2).
- Every user string is a text node (`src/dom.ts`); there is no `innerHTML`. `h()` refuses `style` attributes.
- Works at 390 px with no horizontal scroll, light and dark (system, or the toggle in the header).

```bash
cd site
npm install
npm run dev:mock     # every page against a fake API with fixtures (src/mock.ts); /viewer/ = the real viewer if built, else a placeholder
npm test             # unit tests: rules, DOM safety, routes + redirects, landing, pod rename, mock API, API client
npm run build        # -> dist/  (mock code is compiled out)
```

**Real matches in the dev server** (no Pi, no mock placeholder): build the viewer, play a few short kit matches, run the mock site:
```bash
web/arena/build.sh && site/scripts/make-mock-replays.sh && (cd site && npm run dev:mock)
```
`vite.config.ts` (mock mode only) serves `web/dist/viewer/` at `/viewer/` (falls back to `mock-viewer/` with a console hint when it isn't
built) and `site/.mock-replays/<id>.bin` at `/api/matches/<id>/replay.bin` (404 for ids without a file: the viewer shows its error).
`make-mock-replays.sh` needs Python with numpy + safetensors (`PY=path/to/python`); it plays matches 497-500 (a 7-point game, or best of 3
to 7) with the packaged base model if the kit has one, else `$OSO_LOCAL_BASE` (a local `.safetensors`), else the bot, versus the bot
(`OSO_MODEL_A` / `OSO_MODEL_B` override: a file, `base` or `bot`). It also writes `matches.json`; `mock.ts` uses it so those matches'
names, score, games and rally list are what plays in the viewer. Both are gitignored. The Pi serves the same bundle, `web/dist/viewer/`, at `/viewer/`.

**Site + real viewer under the production CSP** (no Pi needed): a mock-API build, the real viewer bundle and a real
match replay, served with the Pi's headers by `scripts/serve-pi.mjs` (dev tool, not for production):
```bash
(cd .. && web/arena/build.sh)                                   # viewer -> web/dist/viewer/
(cd ../arena/kit && python3 -m oso_arena match bot bot -o ../../out/e2e/match.log \
  && python3 -m oso_arena animate ../../out/e2e/match.log -o ../../out/e2e/match.bin)
npx vite build --mode mock                                     # fake API compiled in: never deploy this dist
OSO_REPLAY=../out/e2e/match.bin node scripts/serve-pi.mjs      # http://localhost:8090/matches/905
```

## Routes

| Path | Page |
|---|---|
| `/` | pod landing |
| `/settings` | pods + account (signed in) |
| `/terms`, `/privacy` | site-wide |
| `/arena` | Arena home |
| `/arena/ladder`, `/arena/models/<id>`, `/arena/matches/<id>`, `/arena/tournaments`, `/arena/tournaments/<id>`, `/arena/my-models`, `/arena/get-started` | Arena pages |

Old paths (`/ladder`, `/models/<id>`, `/matches/<id>`, `/tournaments[/<id>]`, `/my-models`, `/get-started`) redirect
client-side (`history.replaceState`) to the `/arena/...` equivalent. Anything else shows "Not found".

## Landing page notes

- Imagery is **our own renders**: `src/assets/ready.{webp,png}` (640x512, under 15 KB) from
  `oso-rec live --replay-file out/hw/smashes_live1.bin --time 6 --screenshot` (cropped 640x512+130+100, `cwebp -q 80`, PNG quantised to
  64 colours), and `arena-poster.{webp,png}` (1280x720, 40 KB / 74 KB), a frame of the Arena viewer:
  `cargo run --release -p oso-arena -- --replay site/.mock-replays/500.bin --screenshot p.png --time 9 --preset 1 --size 1280x720`, then
  `cwebp -q 78` and `magick p.png -colors 48 -dither None -strip PNG8:arena-poster.png`. Plus inline SVG. No third-party images.
- "Watch AI matches in 3D": the poster, then a button that mounts the viewer iframe in place on the latest finished **ladder** match
  (`GET /api/matches?kind=ladder&limit=5`; no finished match or an API error: the poster links to `/arena`). Never autoplays. The match page,
  the model page and this teaser share `src/viewer.ts` (`mountViewer`: auto, or load-on-tap on phones / data saver / cellular).
- The website no longer mentions camera Replay or the live racquet view (the code and the iPhone app keep them).
- Feature statuses live in `FEATURES` in `src/pages/landing.ts`; flip `status` when something ships. Proof chips use only
  verified figures (500 Hz, 6-axis IMU, Bluetooth LE; about 12 minutes recorded per session on the pod).
- The example report uses the real 2026-10-17 smash session numbers (41 smashes, mean ~69 km/h, max ~95 km/h, estimated);
  the per-swing sparkline is an illustrative deterministic series with exactly that mean and max (labelled as such).
- **TODO(owner):** the hero and feature renders show the racquet OBJ model, whose licence is unknown. Replace them or clear
  the model before a public launch.
- Visuals are sensible defaults from accessibility/touch/performance rules, not picks from the ui-ux-pro-max database
  (not available when this was built).

## For the Pi agent: serving the build

1. **Static files:** serve `site/dist/` at `/`. `assets/*` are content-hashed (`Cache-Control: immutable`);
   `index.html` must revalidate (`no-cache`).
2. **History routing:** answer these page paths with `dist/index.html` (status 200):
   `/`, `/settings`, `/terms`, `/privacy`, `/arena`, `/arena/ladder`, `/arena/models/<id>`, `/arena/matches/<id>`,
   `/arena/tournaments`, `/arena/tournaments/<id>`, `/arena/my-models`, `/arena/get-started`. The old pre-/arena paths
   (`/ladder`, `/models/<id>`, ...) stay served too: the site redirects them client-side. Any other unknown path can also fall back to it; the site shows "Not found".
3. **Viewer bundle:** build it with `web/arena/build.sh` (repo root; needs Rust + wasm-pack) and serve the result,
   `web/dist/viewer/`, at `/viewer/` (same origin, so the rally list can call `iframe.contentWindow.oso_seek(t)`).
   The Match page embeds `/viewer/index.html?match=/api/matches/<id>/replay.bin`; the viewer retries `202` with
   `Retry-After`, shows `{detail}` on errors and accepts a gzip-encoded body. Its exports: `web/arena/README.md`.
4. **CSP:** the build needs no inline script or style. This policy is verified to work (no violations):
   ```
   default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://avatars.githubusercontent.com;
   connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'
   ```
   On `/viewer/` only, add `'wasm-unsafe-eval'` to `script-src` (WebAssembly). Verified with the real viewer and a
   real 20 MB match: no violations.
5. **Sign-in:** the site links to `/auth/github/login?next=<path>`. Please honor `next` (same-origin paths only)
   when redirecting after the callback, defaulting to `/arena/my-models` (the old `/my-models` still works through the redirect).

## For the Pi agent: one additive endpoint requested

The contract has no way to list a user's own models (including pending, rejected and retired ones), which the
My models page needs. Requested, additive:
```
GET /api/models?owner=me      [auth]   200 {"items": [ModelSummary]}   newest first, all statuses
```
Until it exists the page shows the upload form and polls the uploaded model, but the "Your models" list shows an error.

Also used, and assumed optional per the contract text: `GET /api/matches` **without** `model=` (latest matches on
the home page). If that should stay model-only, say so and the home page will show the ladder only.

## For the Pi agent: pods endpoints (additive, Settings page)

```
GET   /api/me/pods                 [auth]          200 {"items":[Pod]}
PATCH /api/me/pods/{id}            [auth + CSRF]   body {"name": "..."}  -> 200 Pod | 400 {"error":"invalid_name"} | 404
Pod = {"id": "pod_123", "hw_id": "OSO-4C99", "name": "Jesus's racquet", "last_seen": "2026-10-17T15:20:00Z" | null}
```
`name` is an account display name stored on the server, linked to the pod's id; it never changes the pod's Bluetooth name.
Validate: trim, 1-40 characters, no control or format characters. `no-store` on the GET. Pairing/linking
(`POST /api/me/pods`, from the Volante app) is future work: until then Settings shows "No pods linked yet: pair one in the Volante
app." The same rows are in `arena/handoff/PROMPT.md` (section 6 table and cross-check).

## Decisions taken (change freely)

- **Ladder sorting** applies within the current page of 50 (the API has no sort parameter); rank order is the server's.
- **Replays on phones / data saver** don't load until the user taps "Load 3D replay" (10-30 MB).
- **Suggested style** is shown as "Smasher?" with a tooltip saying it's a guess from the build (the handoff leaves
  public badges as an open question for the owner).
- **Delete account** (now in Settings) asks the user to type their GitHub login.
- Terms and Privacy carry the handoff's points and are marked as draft text for the owner.
- No branding beyond the name "Volante Arena", a shuttle-orange accent and an SVG favicon. Domain and identity are the
  owner's call.
