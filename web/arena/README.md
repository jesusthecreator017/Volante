# Arena viewer page

`index.html` + `arena.js` + `arena.css` + `timeline.js` are the Arena viewer (`crates/oso-arena`: the shared renderer
plus the scoreboard) as embedded by the Arena site in a same-origin iframe. `web/arena/build.sh` builds the wasm into
`web/arena/pkg/` and assembles the self-contained bundle `web/dist/viewer/`.

## Bundle layout (`web/dist/viewer/`)

```
index.html arena.js arena.css timeline.js
pkg/oso.js pkg/oso_bg.wasm (pkg/snippets/ if emitted)
assets/calib/ assets/models/        # no assets/replay/: the replay comes from ?match= or a drop
```

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
| `oso_score(t)` | `Uint32Array` `[p0, p1]`, empty without point events | empty |
| `oso_replay_error()` | string, `""` when none | the page's own error string |
| `oso_status()` | plain object (see below) | defaults, `loaded: false` |
| `oso_ready` | Promise: resolves at `phase` `"ready"`, rejects with the error message string | pending |

`oso_status()` has every Status field (`loaded time duration playing speed fps frame frame_count skeleton
big_shuttle has_shuttle cameras stroke_count`) plus `phase` (`waiting | preparing | downloading | loading | ready |
error`), `error`, `received` (bytes) and `total` (bytes, `null` when unknown). The page frees the wasm Status object
itself, so callers never call `.free()`.
