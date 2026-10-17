# Continuous deployment to the Pi

Every merge to `main` that touches a deployed part is built on GitHub and installed on the Pi by a self-hosted runner, with a health
check and an automatic rollback. Workflow: `.github/workflows/deploy.yml` (bundle build: `bundle.yml`). The only thing the runner may run as
root is `/usr/local/sbin/volante-deploy` ([ci/volante-deploy](ci/volante-deploy)).

## What deploys when

| Changed in the push | Deploys |
|---|---|
| `site/ web/ crates/ proto/ arena/ deploy/pi/ Cargo.lock` | **Arena** (the bundle from `arena/handoff/make_bundle.sh`) |
| `app/ deploy/pi/` | **Replay LAN server** (`app/dist`, `app/server`, `app/package.json`) |
| the deploy/bundle workflow files | both |

Manual: Actions -> "Deploy to the Pi" -> Run workflow (`target` all/arena/lan, `allow_kit_bump`). Only runs from `main`.

Arena: the bundle must carry `server/` (from `arena/server/`, the Arena server package with `deploy/install.sh`). Until `arena/server/` is in
the repo the Arena deploy ends with "skipped: no server/ in bundle" (green, with a notice in the job summary). `volante-deploy` verifies the
zip's `SHA256SUMS`, compares the bundle's kit wheel version with the installed one, runs `server/deploy/install.sh <bundle dir>` and checks
`http://127.0.0.1:8701/healthz` and `http://127.0.0.1:8700/`. On failure it re-installs the previous successful release (kept under
`/var/lib/volante-deploy/releases/`, last 3) and exits non-zero.
Note for `arena/server/deploy/install.sh`: it currently hard-codes kit `0.4.0` in two places; it should read the version from the bundle
(the wheel in `kit/`), otherwise a deliberate kit bump installs the old pin.

LAN server: replaces only `app/` under `LAN_APP_DIR` (`/home/itsfrosty/oso-b/app`) with `rsync --delete`, excluding `data/` and `node_modules/`
(session data in `/home/itsfrosty/oso-b/data` is outside it), then `systemctl --user -M itsfrosty@ restart oso-replay.service` and checks
`http://127.0.0.1:8090/health`. On failure the pre-deploy copy of `app/` is restored and the unit restarted. User, paths, unit and URLs are
in `/etc/volante-deploy/deploy.conf` ([ci/deploy.conf](ci/deploy.conf)).

## Trust model

- Anyone who can merge to `main` can run code as root on the Pi (the bundle's `server/deploy/install.sh` runs as root). Treat merge rights
  as root on the Pi. The repo is private. Turn on branch protection for `main` (see docs/ci.md).
- `deploy.yml` runs only on `push` to `main` and `workflow_dispatch` (and refuses a dispatch from another branch). Never add `pull_request`
  or fork-triggered workflows that run on the `volante-pi` runner.
- The runner user `volante-ci` has no password and no sudo/adm/docker membership; its sudoers rule is exactly
  `volante-ci ALL=(root) NOPASSWD: /usr/local/sbin/volante-deploy`. The workflow calls only `volante-deploy arena <zip> [--allow-kit-bump]`
  and `volante-deploy lan <tar>`. `volante-deploy` accepts only regular files under `/var/lib/volante-ci/` (no symlinks), copies them into a
  root-only staging directory first and checks the copy.
- No secrets are used by the workflow. The registration token is read from stdin once, during setup.

## Setup on the Pi (once, by the owner)

```bash
# on a machine with the repo, get a registration token: repo Settings > Actions > Runners > New self-hosted runner (valid 1 hour)
sudo deploy/pi/ci/setup-runner.sh --dry-run           # review the plan
echo '<token>' | sudo deploy/pi/ci/setup-runner.sh
```
It is idempotent; it does not touch the PicoURL runner, `/etc/oso-arena`, the Arena data, Funnel or the friend's files.
Then create the `production` environment in the repo settings (no required reviewers; it records deployments). Undo: `uninstall-runner.sh`.
Update `volante-deploy` after a change to it: re-run `setup-runner.sh` (it copies the script, keeps `deploy.conf`).

## Kit version bump

A different kit version starts a new season and retires every model, so the automatic path refuses it (exit 3, red job, nothing installed).
To do it on purpose: Actions -> "Deploy to the Pi" -> Run workflow -> `target: arena`, `allow_kit_bump: true`.

## Roll back by hand

```bash
ls /var/lib/volante-deploy/releases/                   # <timestamp>-arena-<sha> and -lan-; a .ok file marks a healthy release
sudo bash -c 'b=$(echo /var/lib/volante-deploy/releases/<release>/x/*); cd "$b" && bash server/deploy/install.sh "$b"'   # arena
sudo rsync -a --delete --exclude=/data/ --exclude=/node_modules/ --chown=itsfrosty: /var/lib/volante-deploy/releases/<lan release>/prev-app/ /home/itsfrosty/oso-b/app/
sudo -u itsfrosty XDG_RUNTIME_DIR=/run/user/$(id -u itsfrosty) systemctl --user restart oso-replay.service   # lan
```
Or revert the commit on `main`; the next deploy installs the older code.

## Pause deploys

Actions -> "Deploy to the Pi" -> "..." -> Disable workflow (or `gh workflow disable deploy.yml`). Or stop the runner service on the Pi.

## Logs

The Actions log and job summary; on the Pi `/var/log/volante-deploy.log`, `journalctl -u 'actions.runner.*'`, the staged releases in
`/var/lib/volante-deploy/releases/`.

## Tests

`deploy/pi/ci/test_volante_deploy.sh` runs `volante-deploy --dry-run` on fake bundles (good, tampered, kit mismatch, missing `server/`, bad
arguments, symlinked or outside inputs). CI runs it and `shellcheck` on changes to `deploy/pi/**` or the workflows.
