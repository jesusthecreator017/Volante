# Arena viewer page

`index.html` + `arena.js` + `fetch-replay.js` + `players.js` + `timeline.js` + `arena.css` are the Arena viewer (`crates/oso-arena`: the shared renderer
plus the scoreboard) as embedded by the Arena site in a same-origin iframe. `web/arena/build.sh` builds the wasm into
`web/arena/pkg/` and assembles the self-contained bundle `web/dist/viewer/`.

## Bundle layout (`web/dist/viewer/`)

```
index.html arena.js arena.css timeline.js fetch-replay.js players.js
pkg/oso.js pkg/oso_bg.wasm (pkg/snippets/ if emitted)
assets/calib/ assets/models/humanoid/   # no assets/replay/ (the replay comes from ?match= or a drop) and no assets/models/racquet/
```

The racquet OBJ's licence is unknown, so it never ships: Arena draws a procedural racquet (`crates/oso-arena/src/racquet.rs`: 665 mm
long, head 230 x 200 mm, frame tinted in the model's colour, strings, shaft, handle) and never fetches the OBJ. The Replay app keeps the OBJ.

All references are relative, so it works at any path. Build and serve:

```
web/arena/build.sh
python3 -m http.server -d web/dist 8080
# http://localhost:8080/viewer/index.html?match=/api/matches/<id>/replay.bin
```

## CSP

No inline script or style. Needs `script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; connect-src 'self'`
(plus `default-src 'self'; img-src 'self'`).

## Loading

`?match=<url>` is resolved against the page URL; only `http:`/`https:` are accepted. Without it (and without a
stashed drop) the page shows "Drop a match.bin here" and fetches no replay.

- `202` (e.g. `{"status":"building"}`): shows "Preparing match..." and retries after `Retry-After` seconds
  (integer, default 3, clamped to 1-30), with no retry cap.
- Other non-2xx: shows JSON `detail`, else `error`, else `HTTP <status>`. Network failure: "Network error".
- `200`: streamed; shows MB received. A percentage and a determinate `<progress>` appear only when `Content-Length`
  is present and `Content-Encoding` is absent or `identity` (with gzip, `Content-Length` is the compressed size).
- A replay that downloads but does not parse shows "Not a valid replay: <reason>".

The download logic is in `fetch-replay.js` (no DOM: `fetchReplay(url, {signal, fetchFn, sleepFn, onPreparing, onDownloading,
onProgress})`); `arena.js` only maps its callbacks to the overlay. Tests: `node --test web/arena/*.test.mjs` (a fake `fetch` and an
injectable sleep; on older Node `node --test web/arena/` also works).

## Names, builds and colours

A replay written by `animate.py` carries a `match` header object (`docs/data-format.md`): the names and builds of models A and B and
the times at which the replay slots swap sides (change of ends). The page header shows A's name in A's colour (orange) `vs` B's (cyan),
each with the build as `P/S/R/A` (power/speed/reach/accuracy); text is set with `textContent` only. The viewer colours bodies, racquets
and skeleton lines by model, so A keeps its colour across a change of ends, and `oso_score(t)` and the 3D scoreboard are `[A, B]`
(the timeline reads "A 3 – 5 B"). Without a `match` object (old files, the Replay app's files) the header is hidden, colours and score
stay by court slot and the readout says "P1 ... P2". The in-scene scoreboard is by model rather than by court side: it never swaps
owners at a change of ends, and it matches the colours of the page header.

## Camera

Presets only: the Baseline / Side / Top buttons (or keys 1-3 with the canvas focused; 4-5 are the calibrated views in the Replay viewer
when the replay has them). There is no free camera: the canvas ignores drag, wheel and touch, so the page scrolls normally over it.
Switching glides for 0.4 s; with `prefers-reduced-motion` it jumps (`oso_set_reduced_motion(bool)`, wired by the timeline).
The active preset is `oso_status().preset` and `aria-pressed` on its button.

## Drag and drop

Dropping a file anywhere loads it; nothing is uploaded. Before the replay is handed to wasm, the drop wins and aborts
any download or 202 wait. Afterwards the File is stored in IndexedDB (db `oso-viewer`, store `drop`, key `replay`) and
the page reloads; the stash is consumed once on load and takes precedence over `?match=`. If IndexedDB is
unavailable the page says "Reload the page and drop the file again".

## `window` exports

Defined synchronously when the module script runs, before wasm is ready.

| Export | Returns | Before ready |
| --- | --- | --- |
| `oso_seek(t)`, `oso_set_playing(bool)`, `oso_toggle_play()`, `oso_step(n)`, `oso_set_speed(s)`, `oso_preset(n)`, `oso_toggle_skeleton()`, `oso_toggle_big_shuttle()`, `oso_next_stroke()`, `oso_prev_stroke()` | nothing | queued in order, applied once wasm and the replay are up |
| `oso_score(t)` | `Uint32Array` `[A, B]` (`[slot 0, slot 1]` without a `match` header), empty without point events | empty |
| `oso_players()` | `{a: {name, build: {power, speed, reach, accuracy}}, b: {...}}`, or `null` without a `match` header | `null` |
| `oso_replay_error()` | string, `""` when none | the page's own error string |
| `oso_status()` | plain object (see below) | defaults, `loaded: false` |
| `oso_ready` | Promise: resolves at `phase` `"ready"`, rejects with the error message string | pending |

`oso_status()` has every Status field (`loaded time duration playing speed fps frame frame_count skeleton
big_shuttle has_shuttle cameras stroke_count`) plus `phase` (`waiting | preparing | downloading | loading | ready |
error`), `error`, `received` (bytes) and `total` (bytes, `null` when unknown). The page frees the wasm Status object
itself, so callers never call `.free()`.

## Look

`arena.css` follows the website's look (`site/src/style.css`): SF system font stack, glass panels, capsule buttons, light/dark through
`prefers-color-scheme` (the site's manual theme toggle can't reach the iframe). The few tokens it needs (`--text`, `--glass`, `--btn`, the
dark values, the readable A/B name tints) are **copied** into the `:root` blocks at the top of `arena.css`: the viewer imports nothing
from `site/`, so when the site palette changes, update both. The 3D scene colors live in `crates/oso-render/src/state.rs` (court floor,
hall floor and walls) and were tuned to the same green palette (shared with the Replay viewer).
