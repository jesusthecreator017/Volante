#!/usr/bin/env bash
# Reverses setup-runner.sh. Run as root:
#   echo '<removal token>' | sudo deploy/pi/ci/uninstall-runner.sh [--purge-user] [--dry-run]
# The removal token (repo Settings > Actions > Runners > the runner > Remove) unregisters the runner on GitHub; without a token on stdin
# the runner is only removed locally and you delete the stale entry on GitHub by hand. Keeps /var/lib/volante-deploy (release copies)
# and /var/log/volante-deploy.log. --purge-user also deletes the volante-ci user and its home (the runner's _work).
set -euo pipefail
CI_USER=volante-ci
RUNNER_DIR=/var/lib/volante-ci/actions-runner
DRY=0 PURGE=0
for a in "$@"; do case $a in --dry-run) DRY=1 ;; --purge-user) PURGE=1 ;; *) echo "usage: uninstall-runner.sh [--purge-user] [--dry-run]" >&2; exit 2 ;; esac; done
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
run() { if [ "$DRY" = 1 ]; then echo "   [dry-run] $*"; else "$@"; fi; }

if [ -f "$RUNNER_DIR/svc.sh" ] && [ -f "$RUNNER_DIR/.service" ]; then
  echo "== service"; (cd "$RUNNER_DIR" && run ./svc.sh stop; run ./svc.sh uninstall) || true
fi
if [ -f "$RUNNER_DIR/.runner" ] && [ "$DRY" = 0 ] && [ ! -t 0 ]; then
  echo "== unregister"
  IFS= read -r TOKEN || true
  if [ -n "${TOKEN:-}" ]; then ACTIONS_RUNNER_INPUT_TOKEN=$TOKEN runuser -u "$CI_USER" --preserve-environment -- "$RUNNER_DIR/config.sh" remove --unattended || true; fi
  unset TOKEN
else
  echo "== no removal token: delete the runner entry on GitHub by hand"
fi
echo "== files"
run rm -rf "$RUNNER_DIR"
run rm -f /etc/sudoers.d/volante-ci /usr/local/sbin/volante-deploy
run rm -rf /etc/volante-deploy
if [ "$PURGE" = 1 ] && id "$CI_USER" >/dev/null 2>&1; then run userdel -r "$CI_USER"; fi
echo "done"
