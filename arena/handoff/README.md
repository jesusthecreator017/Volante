# Arena Pi handoff (Phase 14.6)

The Pi agent builds the public Arena server (FastAPI + SQLite behind a Cloudflare Tunnel). This folder makes the package it works from.

| file | what |
|---|---|
| `PROMPT.md` | the brief (committed template; `{{KIT_VERSION}}`, `{{CONTRACT}}`, `{{GIT_COMMIT}}`, `{{BASE_MODEL}}`, `{{WHEEL}}` are filled by the build) |
| `make_bundle.sh` | builds `dist/oso-arena-pi/` and `dist/oso-arena-pi.zip` (`dist/` is gitignored) |
| `make_smoke.py` | writes the smoke set (3 valid + 7 invalid files + `expected.json`) deterministically with the kit's own export; `--verify` runs `oso-arena check` on each |

## Build the bundle

```bash
arena/handoff/make_bundle.sh
```

Needs `tools/.venv/bin/python` (or `PYTHON=...`) with numpy, safetensors and `build`; Rust + wasm-pack (the viewer, via `web/arena/build.sh`);
Node + npm (the site, `npm run build` in `site/`; a `--mode mock` dist is refused). The script says what is missing and stops at the first
failure, including any smoke file whose `check` result differs from `expected.json`.

Contents: `PROMPT.md`, `SHA256SUMS`, `contract/` (CONTRACT.md, header.schema.json), `kit/` (the wheel), `base_40min.safetensors` (or
`MISSING_BASE_MODEL.txt` when `arena/kit/oso_arena/data/base_40min.safetensors` is absent), `viewer/`, `site/`, `smoke/`, `docs/` (the READMEs
and the site's `api.ts` / `types.ts` / mock fixtures the brief points to).

## What to send

Send `dist/oso-arena-pi.zip` (or the folder) to the Pi agent as the first message's attachment, with the instruction "unzip it and follow
`PROMPT.md`". If the base model was missing, re-run this script after training finishes and send the new zip; the agent's
`MISSING_BASE_MODEL.txt` note covers working without it. Before sending, answer or consciously defer the **OWNER DECISION** table (section 12
of PROMPT.md: domain/tunnel, GitHub OAuth app, backups, rate limits, tournament schedule, ...). The agent never gets secrets in the bundle.

## Keep in sync

`tests/test_smoke_bundle.py` (in `arena/kit/tests/`, run by `run_all.py`) regenerates the smoke set and checks it against `check`, checks the
generation is byte-deterministic, that `make_bundle.sh` parses, and that PROMPT.md mentions every endpoint in `site/src/api.ts` and every
interface in `site/src/types.ts`. When the site API, the contract or the kit changes, update `PROMPT.md` and rebuild.
