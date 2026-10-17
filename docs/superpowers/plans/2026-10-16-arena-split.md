# Replay / Arena split — implementation plan

> **For agentic workers:** this repo runs phases through its own orchestrator workflow:
> - a phase spec in `.claude/specs/`, then the `developer` agent, then the `qa` agent (at most 2 fix rounds), then a user check
> - this plan lists the phases, their order, interfaces and acceptance
> - each phase spec carries the detailed steps (TDD: tests first where the phase has logic).

**Goal:** split oso into the Replay app (recorded matches) and the Arena game (user-trained models on a public ladder hosted on
the Pi), with a shared renderer, a frozen contract `oso-arena/1`, a pip kit with a coaching skill, and a handoff for the Pi
agent.

**Architecture:**
- **Rust workspace:** `crates/oso-render` (shared), `crates/oso-replay` (app 1), `crates/oso-arena` (viewer).
- **Python:** `arena/kit` is the single official simulator + training kit; the Pi runs the same pinned wheel.
- **Contract:** `arena/contract/` is the source of truth both sides follow.

**Tech stack:**
- Rust 2024 + wgpu 30 + winit 0.30 + hecs (native + wasm)
- Python ≥ 3.10, numpy, safetensors, torch (train extra only)
- Pi: FastAPI + SQLite (built by the Pi agent).

**Spec:** `docs/superpowers/specs/2026-10-16-arena-split-design.md`

## Global constraints
- **Contract `oso-arena/1`:**
  - submission tensors `actor.{0,2,4}.{weight,bias}` float32, shapes [256,37]/[256]/[256,256]/[256]/[6,256]/[6]
  - file ≤ 1 MB, `.safetensors` only
  - header `contract`, `name` (1-40 chars), `description` (0-280), `build` JSON with 4 ints 1-10 summing to 24.
- **Build mapping:** piecewise linear 1 → 6 → 10. At 6 it equals the Phase 13.2 constants exactly.
- **Observation and actions:** observation = Phase 13.2 29 floats + 8 build floats `(s - 5.5) / 4.5` = 37. Actions 6 at 30 Hz,
  and `a = clip(W4·tanh(W2·tanh(W0·o+b0)+b2)+b4, -1, 1)`.
- **No pickle across a trust boundary:** never `torch.load` or pickle a file that came from a user. Kit-shipped weights are
  safetensors.
- **Data licences:** no BFMD-derived file and no racquet OBJ in the kit, the base model, `web/arena` or `dist/oso-arena-pi`.
- **Tests:**
  - Existing tests keep passing at every phase.
  - The synthetic replay swap rule still applies: `assets/replay/replay.bin` is the user's video copy, so swap in the synthetic
    replay and restore it byte-identical (check with `cmp`).
  - Use literal absolute paths with `rm`.
- **Commits:** commit at the end of each phase (the user approved implementation), using the attribution line from the session.

## Review focus (pinned by tests in the owning phase)
1. **A submission that is a pickle renamed to `.safetensors`:** `check` rejects it with a clear reason and never executes it
   (phase 2).
2. **A header with a build that sums to 24 but holds a 0 or 11, or non-integer values like "6.0":** rejected (phase 2).
3. **The same seed + same models + same builds run twice:** byte-identical match log, so watched replays equal the recorded
   result (phase 2).
4. **A replay without point events or builds** (the user's recorded matches) opened in the Arena viewer, or an Arena match
   opened in the Replay app: both open without a crash. Replay shows no score; Arena shows "no score data" (phases 1 and 4).
5. **A 202 "still building" answer from `/api/matches/{id}/replay.bin`:** the viewer retries with backoff and shows a
   "preparing match" state, not an error (phase 4).

---

### Phase 14.1: workspace split (no behaviour change)
- **Rust:** move to `crates/oso-render` (renderer, scene minus scoreboard, assets, replay format/parser, camera, playback core),
  `crates/oso-replay` (app, calib, imu, stats, web_api, bins oso-imu and oso-rec) and `crates/oso-arena` (scoreboard + minimal
  app that opens a replay.bin).
  - Use `git mv`.
  - Root `Cargo.toml` becomes a virtual workspace with members `crates/*` and `proto`.
- **Python:** `tools/badsim/*` → `arena/kit/oso_arena/` as an importable package (`pyproject.toml`, no build changes yet);
  `tools/synth_motion.py` stays (Replay uses it), and the kit imports or vendors only what `animate` needs in phase 2.
- **Web:** `web/` → `web/replay/`, plus a new `web/arena/` (a page hosting the arena wasm). Each has its own `build.sh`.
- **Score readout:** `oso_score` / the score readout move out of Replay into Arena (Replay ignores `point` events).
- **Acceptance:**
  - `cargo test --workspace` and clippy `-D warnings`; wasm builds of both apps; both `build.sh` succeed
  - all Python tests pass from their new paths
  - screenshots: the Replay app with the synthetic replay is unchanged; the Arena app with `out/badsim/match.bin` shows the
    scoreboard.
- **Produces:** crate names and paths later phases use, plus `arena/kit/oso_arena/{sim,bot,train,evaluate,play_match}.py`.

### Phase 14.2: contract, builds and kit core
- **Contract:** `arena/contract/CONTRACT.md` (every observation index), `header.schema.json`.
- **Simulator** `oso_arena.sim`:
  - per-player builds (arrays [n_games, 2, 4]) with `build_params(build) -> dict` (the mapping table)
  - OBS_DIM 37
  - `BadSim(n, seed, builds=...)`.
- **Modules:**
  - `oso_arena.policy.NumpyPolicy.load(path) -> NumpyPolicy` and `.act(obs[n,37]) -> a[n,6]`
  - `oso_arena.check.check_file(path) -> CheckResult(ok: bool, reasons: list[str], meta: dict)`, including the 2-game smoke
    test against the bot
  - `oso_arena.export.export(state, out_path, name, description, build)`
  - `oso_arena.log` (compact match log, versioned): `write(path, MatchLog)`, `read(path) -> MatchLog`
  - `oso_arena.match.play(a, b, seed) -> MatchLog`, where `a`/`b` is a `.safetensors` path or the string `"bot"`
  - `MatchLog` fields (the Pi depends on these, `dist/oso-arena-pi/PROMPT.md` section 3): `contract`, `kit_version`, `seed`,
    `a_sha256`, `b_sha256`, `builds`, `score`, `winner` (0/1/None), `voided`, `reason`, `rallies`, `duration_s`
  - `oso_arena.CONTRACT`, `oso_arena.KIT_VERSION`; `CheckResult.meta` = name, description, build, sha256
  - `oso_arena.animate.to_replay(MatchLog, out_bin)` (from `tools/badsim/play_match.py` + the needed `synth_motion` parts).
- **CLI** `oso-arena`: `check`, `export`, `eval`, `match`, `animate` (`train` arrives in phase 3).
- **Tests:**
  - spec section 5.5, plus review focus 1-3
  - build mapping at 6 equals the 13.2 constants
  - NumpyPolicy matches torch within 1e-5.

### Phase 14.2b: full singles laws, shuttle physics, physical racquet contact
Spec: `.claude/specs/phase14_2b-laws-physics-contact.md`.
- **Laws:** server and receiver still until the serve, net touch, ceiling, serve height/over-the-net, own-side contact and double
  hit tests.
- **Physics:** net-cord physics, turnover drag, and the shot table regenerated with turnover.
- **Contact:** contact quality from body-relative contact geometry (speed cap, noise, mishits); animate IK puts the racquet sweet
  spot on the shuttle (≤ 3 cm).
- **Interface:** obs/action sizes unchanged.

### Phase 14.3: trainer with builds + base model
- **Trainer:** `oso_arena.train` takes `--build`, `--opp-builds random|<p,s,r,a>`, `--init base|<safetensors>` and
  `--reward FILE.py` (the `RewardFn` interface in `oso_arena.rewards`), and saves the full state as safetensors (no pickle).
- **Base model:** widen `init_040.pt` (trusted, ours) to 37 inputs with zero columns, then train with random builds for about
  1-2 h. The gates are in spec section 4.
- **Outputs:** `arena/kit/oso_arena/data/base_40min.safetensors` and `base_40min_full.safetensors`.
  - Size: ~300 KB plus the full model. Commit them (they are ours, no licence issue). If the full model is over 5 MB, use git
    LFS or the dist only.
- **Report:** the per-style-build table (7 builds × win rate against the bot and the base, shot mix).

### Phase 14.4: Arena viewer
- **Loading:** `crates/oso-arena` + `web/arena` open a match from `?match=URL` (fetch, 202 → retry with backoff and a
  "preparing match" state) and by drag-and-drop of a local `match.bin`. Nothing is uploaded.
- **Display:** shows the scoreboard and both players' name and build (from new optional header fields in the replay or a JSON
  sidecar; pick the smallest format change and document it in `docs/data-format.md`).
- **Racquet:** a procedural racquet mesh (frame ellipse + handle) replaces the OBJ in Arena builds, because the OBJ licence is
  unknown.
- **Tests:** review focus 4-5, plus a unit test for the retry state machine.

### Phase 14.5: kit packaging + coach skill + docs
- **Wheel:** `pyproject` metadata, `oso-arena` console script, extras `[train]`; built with `python -m build`.
- **Docs:** README quickstart, TRAINING.md, LICENSE (MIT unless the user says otherwise; ask at the phase start).
- **Coach skill:** `skills/oso-arena-coach/` with `SKILL.md`, `styles.md`, `rewards/<7 styles>.py`, `diagnose.md`.
- **Tests:**
  - every style reward file imports and runs 1 training iteration
  - a clean venv installs the wheel and runs `oso-arena check base`.

### Phase 14.6: Pi handoff bundle
`dist/oso-arena-pi/` contains:
- PROMPT.md, refreshed with final paths and versions
- the contract
- the kit wheel
- `base_40min.safetensors`
- the built `web/arena`
- a `smoke/` folder: 3 valid and 6 invalid submission files with the expected check results.

Zip it like the app handoff.

## Order and dependencies
- **Order:** 14.1 → 14.2 (incl. best-of-3 match level) → 14.2b → 14.3 → (14.4 can run in parallel with 14.3; it only needs 14.2's log/animate output) → 14.5 →
  14.6.
- **Pi agent:** it can start now from the draft `dist/oso-arena-pi/PROMPT.md`, building the site against the contract with a
  mock policy until the 14.6 bundle arrives.
