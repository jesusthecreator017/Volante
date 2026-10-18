# CI/CD (GitHub Actions)

The repo is private: Actions minutes count against the plan. Merges to `main` deploy to the Pi (see Deploy below).

## CI (`.github/workflows/ci.yml`)
Runs on pull requests, pushes to `main` and manual dispatch. A `changes` job (paths filter) decides which jobs run, so a docs-only PR
costs almost nothing; dispatch runs everything. Jobs: `Rust (build, test, clippy)`, `Rust wasm (clippy, viewer bundle)`,
`Python (arena-kit)`, `Python (tools)`, `site`, `app`, `web/arena (node --test)`, and `Apple` (macOS, only for `apple/**` changes).

Notes:
- The Python jobs tolerate the five macOS-recorded bit-exact golden tests on Linux with a warning (`.github/scripts/kit_tests.py`).
- `cargo fmt` is not enforced (the tree is not rustfmt-formatted).
- `tools/test_video.py` is not run (needs rendered videos and ONNX pose models); `tools/test_strokes.py` skips its BFMD parts.
- Not covered: `oso-replay` for wasm, `web/replay/build.sh`, and the firmware (release only).

## Deploy to the Pi (`.github/workflows/deploy.yml`)
On a push to `main` touching `site/ web/ crates/ proto/ arena/ app/ deploy/pi/` (and by manual dispatch), a self-hosted runner on the Pi
installs the Arena bundle and/or the Replay LAN server through `/usr/local/sbin/volante-deploy`, health-checks, and rolls back on failure.
Kit version bumps are refused unless dispatched with `allow_kit_bump`. The bundle build is the reusable `bundle.yml` (also used by the
release). Setup, trust model, rollback and pausing: [deploy/pi/CD.md](../deploy/pi/CD.md). CI job `Pi deploy scripts` runs shellcheck and
`deploy/pi/ci/test_volante_deploy.sh`.

## Release (`.github/workflows/release.yml`)
```
git tag v0.4.1 && git push origin v0.4.1
```
Jobs: `kit` (wheel + sdist, checked in a fresh venv), `web` (viewer zips and site zip), `pi-bundle`
(`bundle.yml`, i.e. `arena/handoff/make_bundle.sh`), `firmware` (esp toolchain, `cargo build --release`, app image), then `release` creates the GitHub
Release with generated notes, every file below and `SHA256SUMS.txt`.

Assets: `oso_arena-*.whl`, `oso_arena-*.tar.gz`, `oso-viewer-arena-<tag>.zip`, `oso-viewer-replay-<tag>.zip`, `site-<tag>.zip`,
`oso-arena-pi-<tag>.zip`, `oso-fw-<tag>.bin`, `partitions.csv`, `FLASHING.txt`, `SHA256SUMS.txt`.

Dry run (builds everything, publishes nothing): Actions tab -> Release -> Run workflow (keep "dry_run" ticked), or
`gh workflow run release.yml --ref <branch>`. The artifacts stay on the run page.

### Flash the release firmware
`oso-fw-<tag>.bin` is the app image only. On a pod that already runs oso-fw:
```
espflash write-bin 0x10000 oso-fw-<tag>.bin
```
`0x10000` is the `factory` partition in `partitions.csv` (the workflow reads it from there and checks the image fits in 0x180000).
Do not write a merged image with a partition table or erase the flash: the `imu` partition (0x190000, 0x670000) holds the recorded
session. A blank board needs the bootloader and partition table once: `cd firmware && cargo run --release` from a checkout of the tag.

## Require the checks on `main`
Repo settings are not changed by the workflows. Once the checks have run at least once, as a repo admin:
```
gh api -X PUT repos/jesusthecreator017/oso/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": { "strict": false, "contexts": ["rust", "wasm", "python", "site"] },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
```
(Branch protection on a private repo needs a paid plan. Replace the owner/repo if it differs.)

## Pinned actions
The release workflow pins every third-party action to a commit SHA with the version in a comment; wasm-pack and espflash are pinned
release binaries (`.github/actions/wasm-pack`, `release.yml`). Bump them by hand.
