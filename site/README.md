# oso Arena website (frontend)

The public Arena site: Home, Ladder, Model, Match (3D viewer + rally list), Tournaments, My models (upload),
Get started, Terms, Privacy. Built against the API contract in `oso-web-app-PROMPT.md` section 6. The Pi agent's
server serves the **API** and this **static build**; it does not render pages.

- TypeScript + Vite, no UI framework, no runtime dependencies (~11 KB gzipped JS, ~4 KB CSS).
- Every user string is a text node (`src/dom.ts`); there is no `innerHTML`. `h()` refuses `style` attributes.
- Works at 390 px with no horizontal scroll, light and dark (system, or the toggle in the header).

```bash
cd site
npm install
npm run dev:mock     # every page against a fake API with fixtures (src/mock.ts) + a viewer placeholder
npm test             # 11 unit tests: rules, DOM safety, router, API client (CSRF, errors, 429)
npm run build        # -> dist/  (mock code is compiled out)
```

## For the Pi agent: serving the build

1. **Static files:** serve `site/dist/` at `/`. `assets/*` are content-hashed (`Cache-Control: immutable`);
   `index.html` must revalidate (`no-cache`).
2. **History routing:** answer these page paths with `dist/index.html` (status 200):
   `/`, `/ladder`, `/models/<id>`, `/matches/<id>`, `/tournaments`, `/tournaments/<id>`, `/my-models`,
   `/get-started`, `/terms`, `/privacy`. Any other unknown path can also fall back to it; the site shows "Not found".
3. **Viewer bundle:** serve the oso team's viewer at `/viewer/` (same origin, so the rally list can call
   `iframe.contentWindow.oso_seek(t)`). The Match page embeds `/viewer/index.html?match=/api/matches/<id>/replay.bin`.
4. **CSP:** the build needs no inline script or style. This policy is verified to work (no violations):
   ```
   default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://avatars.githubusercontent.com;
   connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'
   ```
   Add whatever the viewer needs (e.g. `'wasm-unsafe-eval'` in `script-src` for WebAssembly) on `/viewer/` only.
5. **Sign-in:** the site links to `/auth/github/login?next=<path>`. Please honor `next` (same-origin paths only)
   when redirecting after the callback, defaulting to `/my-models` as in the contract.

## For the Pi agent: one additive endpoint requested

The contract has no way to list a user's own models (including pending, rejected and retired ones), which the
My models page needs. Requested, additive:
```
GET /api/models?owner=me      [auth]   200 {"items": [ModelSummary]}   newest first, all statuses
```
Until it exists the page shows the upload form and polls the uploaded model, but the "Your models" list shows an error.

Also used, and assumed optional per the contract text: `GET /api/matches` **without** `model=` (latest matches on
the home page). If that should stay model-only, say so and the home page will show the ladder only.

## Decisions taken (change freely)

- **Ladder sorting** applies within the current page of 50 (the API has no sort parameter); rank order is the server's.
- **Replays on phones / data saver** don't load until the user taps "Load 3D replay" (10-30 MB).
- **Suggested style** is shown as "Smasher?" with a tooltip saying it's a guess from the build (the handoff leaves
  public badges as an open question for the owner).
- **Delete account** asks the user to type their GitHub login.
- Terms and Privacy carry the handoff's points and are marked as draft text for the owner.
- No branding beyond the name "oso Arena", a shuttle-orange accent and an SVG favicon. Domain and identity are the
  owner's call.
