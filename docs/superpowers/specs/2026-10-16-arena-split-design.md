# oso split: Replay app + Arena (AI badminton game) — design

Date: 2026-10-16. Status: approved in conversation, pending written-spec review.

## 1. Goal

Split oso into two separate products that share a renderer.

- **Replay:** recorded real matches reconstructed in 3D (cameras, pose, shuttle, racquet IMU, strokes, stats). This is today's
  pipeline, minus everything AI.
- **Arena:** a public game.
  - Users start from the provided base model (the Phase 13.2 40-min policy), pick a stat build, and train their own model with
    a pip kit.
  - They submit the weights to a public site hosted on the user's Raspberry Pi 5.
  - Models play an always-on Elo ladder plus a weekly top-8 tournament, and every match can be watched in 3D.

### Decisions (user)
- **Audience:** a public website (untrusted uploads).
- **Simulator:** Python is the single official simulator (approach A). The kit and the Pi run the same pinned package.
- **Submission:** fixed network, weights only, `.safetensors`, plus a stat build.
- **Competition:** ladder + weekly tournaments. The site is hosted on the Pi 5.
- **Defaults accepted:** GitHub sign-in, one active model per user, upload limits, weekly top-8 bracket.
- **Kit extras:** a Claude skill (`oso-arena-coach`) with play styles. Trade-offs come from stat builds and training.
- **Pi work:** everything Pi-side (sections 6 and 7) is delegated to a separate Claude Code agent via a handoff prompt
  (`dist/oso-arena-pi/PROMPT.md`). This repo delivers the contract, the kit and the viewer the Pi agent consumes.

### Non-goals (v1)
- doubles (a later phase, new contract version)
- custom network architectures
- live in-browser simulation
- doubles
- an admin web UI
- payments
- a Rust simulator port (that is approach B; it stays a later upgrade if the Pi gets too slow).

## 2. Repository layout after the split

```
oso/
├─ crates/
│  ├─ oso-render/   lib: wgpu renderer, hall/court, bodies, racquets, shuttle, cameras,
│  │                replay.bin format + parser (events incl. type 3 `point`)
│  ├─ oso-replay/   app 1 (native + wasm): calibration, IMU fusion/sync/swings, stats panel,
│  │                bins oso-imu, oso-rec; ignores `point` events
│  └─ oso-arena/    app 2 (wasm + native): match viewer with scoreboard (moved from today's
│                   src/scene/scoreboard.rs), opens a match.bin from a URL or drag-and-drop
├─ proto/, firmware/     unchanged (Replay)
├─ tools/                Replay's Python pipeline (badsim/ removed from here)
├─ arena/
│  ├─ kit/               pip package `oso-arena` (section 5), incl. skills/oso-arena-coach/
│  └─ contract/          CONTRACT.md + JSON schema of the submission header (source of truth)
├─ web/replay/, web/arena/   two front-ends (web/arena = viewer page used by the Pi site)
└─ dist/oso-arena-pi/    handoff for the Pi agent (PROMPT.md, contract copy, kit wheel, base model)
```

- **Moving files:** use `git mv` to keep history. Every existing test keeps passing after the move, and only paths and imports
  change.
- **What moves to Arena:** the scoreboard and the `point`-event score readout (`oso_score`, the timeline score text, stats
  points). Replay keeps parsing `point` events but does not show a score.
- **Workspace:** root `Cargo.toml` members = `crates/*`, `proto`. `firmware` stays excluded.

## 3. Contract `oso-arena/1` (shared by kit and Pi)

This is the source of truth in `arena/contract/CONTRACT.md`. Any change to the observation, actions, physics or build mapping
bumps the version and starts a new season.

### 3.1 Submission file
A single `.safetensors` file of at most 1 MB.

**Tensors:** exactly these, all float32, nothing extra:

| name | shape |
|---|---|
| `actor.0.weight` | [256, 37] |
| `actor.0.bias` | [256] |
| `actor.2.weight` | [256, 256] |
| `actor.2.bias` | [256] |
| `actor.4.weight` | [6, 256] |
| `actor.4.bias` | [6] |

**Network:** `h1 = tanh(W0·o + b0)`, `h2 = tanh(W2·h1 + b2)`, `a = clip(W4·h2 + b4, -1, 1)`. This is the deterministic
mean action, the same as `ActorCritic.act(deterministic=True)` in today's `tools/badsim/train.py` plus the simulator's clip.
The `mlp()` hidden activation is tanh (`tools/badsim/train.py:43`).

**Header metadata** (string → string, as safetensors allows):
- `contract` = `"oso-arena/1"`
- `name`: 1-40 printable characters
- `description`: 0-280 characters, optional
- `build`: JSON `{"power":p,"speed":s,"reach":r,"accuracy":a}`. Each value is an integer 1-10, and the sum is exactly 24.

Unknown keys are ignored.

### 3.2 Stat build mapping
Piecewise linear, s = 1 → 6 → 10. A 6 in every stat equals the Phase 13.2 constants.

| stat | 1 | 6 | 10 |
|---|---|---|---|
| power: max launch speed at contact ≥ 2.6 m (m/s) | 60 | 90 | 110 |
| power: max launch speed below 1.0 m (m/s) | 20 | 25 | 30 |
| speed: V_MAX (m/s) / A_MAX (m/s²) | 4.0 / 8 | 5.0 / 10 | 6.0 / 12 |
| reach: REACH / REACH_LUNGE (m) | 1.2 / 1.7 | 1.5 / 2.0 | 1.8 / 2.3 |
| accuracy: aim-noise multiplier | 1.4 | 1.0 | 0.6 |

The simulator holds the stats per player (arrays over games × players). The rules, the shuttle physics, REACTION_S, the jump
height and the freezes are the same for everyone.

### 3.3 Observation and action
- **Observation:** the Phase 13.2 observation (29 floats, mirrored per player) plus 8 floats: own and opponent build, each stat
  normalised as `(s - 5.5) / 4.5`. Total **37**. `CONTRACT.md` lists every index with meaning and scaling.
- **Action:** 6 floats at 30 Hz, unchanged from Phase 13: move vx, vz, swing, aim x, aim z, flight time u.

### 3.4 Match rules (BWF singles laws, 15-point games; amended 2026-10-16)
**Format:**
- A match is **best of 3 games**.
- Each game is rally scoring to 15, win by 2, cap 21 (at 20-20 the 21st point wins).
- The winner of a game serves first in the next. The first server of game 1 is random from the seed.

**Service:** singles courts by the server's score parity: right when even, left when odd.

**Intervals and ends:**
- A 60 s interval when the leading score first reaches 8.
- 120 s between games.
- Change of ends after games 1 and 2, and at 8 in game 3.
- These are timeline and side effects only; the simulator is mirror-symmetric.

**Laws enforced** (phase 14.2b):
- server and receiver still until the serve contact
- serve below 1.15 m, over the net and into the diagonal service court
- contact on own side
- net touch
- double hit
- ceiling and obstacles
- net-cord play (the shuttle may clip the tape and go over or fall back)
- a let only at the 150-shot rally cap.

**Physics** (phase 14.2b): the fitted drag model, a shuttle turnover phase after impact, net-cord contact, and **physical racquet
contact**:
- The model gives shot intent (aim x/z, flight time).
- The outcome's speed limit and accuracy depend on a contact quality computed from the contact point relative to the body
  (ideal, cramped, late, stretched, backhand). Very poor contact is a mishit.
- In replays the racquet sweet spot meets the shuttle.

**Void:** a match over 150 simulated minutes of play. A rally over 150 shots is a let.

**Randomness:** only the simulator's execution noise, driven by the match seed, so same models + same builds + same seed give
the identical match. The kit tests this.

**MatchLog** records:
- contract, kit version, seed, both builds, both hashes and names
- `score` = games won, plus per-game scores
- per-rally details (game, ends, interval before).

### 3.5 Validation (kit `check` == Pi upload check, the same code)
1. The file is at most 1 MB and parses with `safetensors` only. Never use pickle or torch.load.
2. It has exactly the tensors, shapes and dtype listed in 3.1.
3. Every value is finite with |w| ≤ 1000.
4. The header passes: contract matches, the name and description are valid, the build is valid.
5. Smoke test: 2 games against the scripted bot complete without exceptions or non-finite actions.

It returns `ok` or a list of human-readable reasons.

## 4. Base model (our side)
- **Source:** `out/badsim/init_040.pt`, the Phase 13.2 40.5-min model (obs 29), trained under the old physics. It is a warm
  start only: it must be retrained on the 14.2b simulator (laws, net cord, turnover, contact quality).
- **Adapting it:** widen the first layer to 37 inputs, with the 8 new columns set to zero. Then continue training with a random
  valid build per player per game, the 13.2 rewards and a constant BASE_REWARD, for about 1-2 h with the speed-ups.
- **Gates** (200 games each, averaged over random builds):
  - no worse than the 40-min model at the all-6 build, within noise, on win rate against the bot and on placement distance
  - win rate against the scripted bot at least 80 % at the all-6 build.
  - Report the per-build results for the 7 style builds in section 5.3.
- **Outputs:** `base_40min.safetensors` (the submission format, all-6 build, name "House Base") and
  `base_40min_full.safetensors` (actor + critic + log_std, so it can be fine-tuned without pickle).
- **House players** on the ladder: the base model and the scripted bot (with an all-6 build).

## 5. User kit `oso-arena` (pip)

### 5.1 Package
- **Python:** 3.10 or newer.
- **Dependencies:** `numpy` and `safetensors`. The `train` extra adds `torch`.
- **Distribution:** a wheel from the site's "Get started" page. It is built from this repo; `dist/oso-arena-pi/` carries the
  wheel for the Pi.

| module | content |
|---|---|
| `oso_arena.sim` | the simulator (moved from tools/badsim/sim.py, with builds), frozen per contract |
| `oso_arena.bot` | the scripted bot |
| `oso_arena.policy` | `NumpyPolicy`: loads and validates a `.safetensors` file and acts. The Pi uses the same class |
| `oso_arena.train` | the PPO trainer (13.2 state: MPS update, 1024 games, checkpoints, resume, early stop) with `--build`, `--opp-builds random\|<build>`, `--init base\|<file>`, `--reward FILE.py` |
| `oso_arena.rewards` | the default reward and the documented `RewardFn` interface |
| `oso_arena.export` / `check` | training state → submission file; the 3.5 checks |
| `oso_arena.evaluate` / `match` | local games; metrics (win rate, rally, shot mix, placement distance, opponent run, faults); `match` writes `match.bin` |
| `oso_arena.animate` | match log → bodies and swings (from tools/synth_motion.py, refactored) → `replay.bin` |
| `oso_arena.log` | the compact match-log format (versioned, gzip JSON or npz), written by `match`, read by `animate` |

**CLI:**
```
oso-arena train  --init base --build 9,6,5,4 --reward my_reward.py --minutes 60 --out runs/try1
oso-arena export runs/try1/best -o mymodel.safetensors --name "Drop Shot Dan"   # build taken from the run
oso-arena check  mymodel.safetensors
oso-arena eval   mymodel.safetensors --vs base --games 100
oso-arena match  mymodel.safetensors base -o match.log.gz --seed 1
oso-arena animate match.log.gz -o match.bin
```

### 5.2 Reward plug-in
- **Interface:** a user file defines `reward(ev) -> np.ndarray[n_games, 2]`. `ev` carries per-step arrays of hits, landings,
  rally ends, positions and builds.
- **What it can change:** it shapes training only. The simulator and the contract are untouchable.
- **Safety warning:** the kit warns when a reward lets total per-rally shaping exceed 1 point (estimated from a short rollout),
  because it can teach cooperation over winning.

### 5.3 Claude skill `skills/oso-arena-coach/`
- **`SKILL.md`, the agent workflow:**
  1. Interview the user: which style, which weaknesses they accept, their compute and time budget.
  2. Choose the build and the reward template.
  3. Short train/eval loops of 15-30 min.
  4. Diagnose and iterate.
  5. Export, `check`, then submit through the site.
- **Rules for the agent:**
  - never change the simulator or the contract
  - keep per-rally shaping below 1 point
  - never load `.pt` or pickle files from others
  - respect the user's budget
  - report honestly (shot mix and win rates, not hopes).
- **`styles.md`:** for each style, the build, the reward focus, target metrics, the expected weakness, counters, and the
  matching file in `rewards/`.

  | style | P/S/R/A | reward focus | weakness |
  |---|---|---|---|
  | Smasher / Attacker | 9/6/5/4 | points won with steep fast shots, sideline placement | defence, unforced errors |
  | Wall / Defender | 3/8/8/5 | returns under pressure, deep lifts and clears | few winners |
  | Net Dominator | 3/6/7/8 | tight net shots, front-court kills | deep clears over the head |
  | Controller / Placer | 4/6/5/9 | landing far from the opponent, drop/clear mix | no finishing power |
  | Speedster / Tempo | 4/9/5/6 | fast flat drives and pushes, quick returns | reach on wide smashes |
  | Counter-attacker | 6/7/7/4 | defend, then attack weak replies | accuracy |
  | All-rounder | 6/6/6/6 | balanced (the base reward) | no standout |

- **`rewards/<style>.py`:** one runnable template per style.
- **`diagnose.md`:** a symptom → cause → fix table from Phases 13-13.2. It covers:
  - net camping after the base-reward anneal
  - cooperative rallies from per-shot rewards
  - serve faults
  - collapse after a mid-run rule change (lower the learning rate, resume from the last good checkpoint)
  - smash spam with low noise
  - hitting straight at the opponent.

  It also explains how to read `eval`.

### 5.4 Kit docs
- `README` (a 5-minute quickstart)
- `CONTRACT.md` (a copy)
- `TRAINING.md` (lessons and compute tips, e.g. MPS for the update)
- `LICENSE`: decide the kit licence before the public release; MIT is suggested. The base model and kit contain no BFMD data
  and no racquet OBJ.

### 5.5 Kit tests
- the existing badsim tests, ported, with builds
- a build mapping test (s = 6 equals the 13.2 constants, monotonic)
- export → check round trip; `check` rejects each bad case (extra tensor, wrong shape, NaN, bad build sum, wrong contract,
  oversize, pickle file)
- `NumpyPolicy` matches the torch actor within 1e-5
- determinism: same seed, models and builds give a byte-identical match log
- `animate` produces a `match.bin` that `oso-arena`'s viewer and `--check-replay` accept.

## 6. Pi match runner and ladder (handoff, built by the Pi agent)
- **Workers:**
  - 2 match worker processes plus a scheduler, on a Pi 5.
  - Models run with `NumpyPolicy` and the simulator from the pinned kit version. There is no torch on the Pi.
  - Workers run as a low-privilege user with CPU-time and memory rlimits.
  - Measure time per game, with a target of 10 s or less, and report it.
- **Matches:** stored as a DB row plus a compact log (section 3.4 fields, score, winner, rallies, duration).
- **Ladder:**
  - Elo starting at 1200, K = 32 for a model's first 20 games and 16 after.
  - New models play 10 placement games.
  - Every minute, pair the models with the fewest recent games against a random one of their 5 nearest within ±200.
  - House players stay permanently: the base model and the bot.
  - One active model per user; an upload retires the previous model, keeping its history.
  - A file identical to the base model or to another user's active model (same hash) is rejected.
- **Seasons:** a season is the contract plus the kit version. A new season resets ratings and requires re-submission. Old
  seasons stay viewable.
- **Tournament:**
  - weekly at Sunday 18:00, Pi local time (configurable)
  - the top 8 by rating with at least 20 games
  - single elimination, best of 3, seeded by rating
  - tournament games do not change Elo
  - the final is featured on the home page.
- **Watching:**
  - `replay.bin` is built on demand with `oso_arena.animate` from the log.
  - An LRU cache up to 5 GB, served compressed.
  - Tournament finals and the latest 20 ladder games stay cached.
  - `GET` returns 202 with a retry hint while building.

## 7. Pi site (handoff, built by the Pi agent)
- **Stack:**
  - Python FastAPI + SQLite (WAL); systemd services `web`, `worker@1..2`, `scheduler`.
  - Public access through a Cloudflare Tunnel: no open ports and automatic HTTPS. The user needs a domain on Cloudflare
    (about $10/yr).
- **Auth:**
  - GitHub OAuth. The site stores only the GitHub id, login and avatar.
  - Session cookie: HttpOnly, Secure, SameSite=Lax, plus a CSRF token on state changes.
  - Users can delete their account, which retires and removes their models.
- **Uploads:**
  - `POST /api/models` (at most 1 MB, `.safetensors` only; the build and name come from the header). Status goes
    `pending` → `active` or `rejected`, with reasons from 3.5.
  - Limits: 5 uploads per user per day, plus per-IP API rate limits.
  - All user text is escaped and length-capped.
- **Pages** (`web/arena/` static files + the `oso-arena` wasm viewer):
  - Home: the featured final, top-10, latest matches
  - Ladder: with a season picker
  - Model: rating chart, matches, build, description
  - Match: the 3D viewer, score sheet, build of each side
  - Tournament: the bracket
  - My models: upload, status, retire
  - Get started: kit wheel, CONTRACT, rules, terms.
- **API (JSON):**
  - Public reads: `GET /api/ladder?season=`, `/api/models/{id}`, `/api/matches?model=`, `/api/matches/{id}`,
    `/api/matches/{id}/replay.bin`, `/api/tournaments/current`, `/api/tournaments/{id}`.
  - Signed-in: `GET /api/me`, `POST /api/models`, `DELETE /api/models/{id}`.
  - Auth: `/auth/github/login`, `/auth/github/callback`, `/auth/logout`. `GET /healthz` reports workers and queue.
- **Admin:** a CLI on the Pi: hide a model, ban a user, start a new season, run a tournament now.
- **Ops:**
  - Secrets live in an env file on the Pi, never in git.
  - Nightly backup of the DB and model files to a second location.
  - Rotated logs.
- **Terms:** models and results are public, one account per person, and matches can be voided for exploiting bugs.

## 8. Our deliverables and order
1. **Split:** the crates, the `arena/` move, `web/replay` and `web/arena`. All existing tests pass.
2. **Contract and builds:**
   - `CONTRACT.md` + header schema
   - the simulator with per-player builds and the 37-dim observation
   - `NumpyPolicy`, `check`, `export`, the log format, `animate`, the `match` CLI.
3. **Base model:** adapt and train it (section 4), then the two safetensors files.
4. **The `oso-arena` viewer:** open `match.bin` by URL or drag-and-drop, with the scoreboard and both builds shown. Load from
   `/api/matches/{id}/replay.bin` with a 202 retry.
5. **Kit packaging:** wheel, README, TRAINING, the coach skill (styles, rewards templates, diagnose).
6. **Pi handoff:** `dist/oso-arena-pi/PROMPT.md` (sections 3, 6 and 7 of this spec, the API, acceptance tests, the security
   checklist) plus the contract, the kit wheel, the base model and the built `web/arena`. Like the app handoff, `dist/` is
   gitignored.

Each step is a phase with its own spec, developer, QA, and user check, as usual in this repo.

## 9. Risks
- **Python speed on the Pi:** if a game takes more than about 10 s, ladder throughput drops. The fallbacks are fewer workers'
  games per hour, or approach B later.
- **Base-model adaptation to random builds** may lose some strength. The gate is in section 4. If it fails, train longer or
  narrow the build ranges.
- **The 25 % serve-fault habit** of the 40-min model carries over. Users can train it away, and `diagnose.md` covers it.
- **Exploits:** a degenerate strategy may dominate (e.g. one build and style). Mitigation: the season mechanism lets us
  rebalance the build mapping in `oso-arena/2`.
- **Racquet OBJ licence:** unknown. The public Arena viewer must use a replacement or a procedural racquet before the site goes
  public. Add this to step 4.
