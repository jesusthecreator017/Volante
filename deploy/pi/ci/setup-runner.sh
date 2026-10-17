#!/usr/bin/env bash
# One-time (and re-runnable) setup of the CD runner on the Pi. Run as root, by the owner:
#   echo '<registration token>' | sudo deploy/pi/ci/setup-runner.sh          # token: repo Settings > Actions > Runners > New
#   sudo deploy/pi/ci/setup-runner.sh --dry-run                               # prints what it would do, changes nothing
# Creates the system user volante-ci, installs the GitHub Actions runner (pinned, sha256 checked) as a service running as that user,
# and installs volante-deploy + /etc/volante-deploy/deploy.conf + the sudoers rule. Does not touch the PicoURL runner,
# /etc/oso-arena, the Arena data, Tailscale Funnel or the friend's files. See deploy/pi/CD.md.
set -euo pipefail

RUNNER_VERSION=2.338.0
RUNNER_SHA256=628b4a7258487b80c1d3c221095a7ded349f5ae0175fd3dfab708d07428f041b   # linux-arm64, from the release notes of v$RUNNER_VERSION
REPO_URL=https://github.com/jesusthecreator017/Volante
RUNNER_NAME=pi5server-volante
RUNNER_LABELS=volante-pi
CI_USER=volante-ci
CI_HOME=/var/lib/volante-ci
RUNNER_DIR=$CI_HOME/actions-runner
HERE=$(cd "$(dirname "$0")" && pwd)

DRY=0
case ${1:-} in --dry-run) DRY=1; shift ;; esac
[ $# -eq 0 ] || { echo "usage: setup-runner.sh [--dry-run]" >&2; exit 2; }
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
[ "$(uname -m)" = aarch64 ] || echo "warning: this is $(uname -m), the pinned runner is linux-arm64" >&2

step() { echo "== $*"; }
run() { if [ "$DRY" = 1 ]; then echo "   [dry-run] $*"; else "$@"; fi; }

step "packages (rsync, unzip, curl are used by volante-deploy)"
need=()
for c in rsync unzip curl; do command -v "$c" >/dev/null 2>&1 || need+=("$c"); done
if [ ${#need[@]} -gt 0 ]; then run apt-get install -y "${need[@]}"; else echo "   present"; fi

step "user $CI_USER (no password, no sudo/adm/docker membership)"
if id "$CI_USER" >/dev/null 2>&1; then echo "   exists"
else run useradd --system --home-dir "$CI_HOME" --create-home --shell /bin/bash --user-group "$CI_USER"; fi
run passwd -l "$CI_USER" >/dev/null 2>&1 || true
for g in sudo adm docker; do
  if id -nG "$CI_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$g"; then echo "   removing $CI_USER from $g"; run gpasswd -d "$CI_USER" "$g"; fi
done

step "runner $RUNNER_VERSION in $RUNNER_DIR"
if [ -f "$RUNNER_DIR/.volante-runner-version" ] && [ "$(cat "$RUNNER_DIR/.volante-runner-version")" = "$RUNNER_VERSION" ]; then echo "   already installed"
else
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  TGZ=actions-runner-linux-arm64-$RUNNER_VERSION.tar.gz
  run curl -fsSL -o "$TMP/$TGZ" "https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/$TGZ"
  if [ "$DRY" = 1 ]; then echo "   [dry-run] verify sha256 $RUNNER_SHA256"
  else echo "$RUNNER_SHA256  $TMP/$TGZ" | sha256sum -c - || { echo "runner download does not match the pinned sha256" >&2; exit 1; }; fi
  run install -d -o "$CI_USER" -g "$CI_USER" -m 0750 "$RUNNER_DIR"
  run tar -xzf "$TMP/$TGZ" -C "$RUNNER_DIR" --no-same-owner
  run chown -R "$CI_USER:$CI_USER" "$RUNNER_DIR"
  [ "$DRY" = 1 ] || echo "$RUNNER_VERSION" > "$RUNNER_DIR/.volante-runner-version"
fi

step "register the runner ($REPO_URL, labels $RUNNER_LABELS, name $RUNNER_NAME)"
if [ -f "$RUNNER_DIR/.runner" ]; then echo "   already registered (remove $RUNNER_DIR/.runner and re-run to re-register)"
elif [ "$DRY" = 1 ]; then echo "   [dry-run] read the registration token from stdin, run config.sh as $CI_USER with it in the environment (not argv)"
else
  [ ! -t 0 ] || { echo "pass the registration token on stdin: echo TOKEN | sudo $0" >&2; exit 1; }
  IFS= read -r TOKEN
  [ -n "$TOKEN" ] || { echo "empty token" >&2; exit 1; }
  # config.sh reads ACTIONS_RUNNER_INPUT_* from its environment; runuser -u keeps the environment, and nothing lands on a command line
  ACTIONS_RUNNER_INPUT_TOKEN=$TOKEN runuser -u "$CI_USER" --preserve-environment -- \
    "$RUNNER_DIR/config.sh" --url "$REPO_URL" --labels "$RUNNER_LABELS" --name "$RUNNER_NAME" --unattended --replace
  unset TOKEN
fi

step "service (runs as $CI_USER)"
if [ "$DRY" = 1 ]; then echo "   [dry-run] (cd $RUNNER_DIR && ./svc.sh install $CI_USER && ./svc.sh start)"
elif [ -f "$RUNNER_DIR/.service" ]; then echo "   already installed ($(cat "$RUNNER_DIR/.service"))"
else (cd "$RUNNER_DIR" && ./svc.sh install "$CI_USER" && ./svc.sh start); fi

step "volante-deploy, deploy.conf, sudoers"
run install -d -o root -g root -m 0755 /etc/volante-deploy
run install -d -o root -g root -m 0700 /var/lib/volante-deploy
run install -o root -g root -m 0755 "$HERE/volante-deploy" /usr/local/sbin/volante-deploy
if [ -e /etc/volante-deploy/deploy.conf ]; then echo "   /etc/volante-deploy/deploy.conf exists: left as it is"
else run install -o root -g root -m 0644 "$HERE/deploy.conf" /etc/volante-deploy/deploy.conf; fi
SUDOERS=$(mktemp)
echo "$CI_USER ALL=(root) NOPASSWD: /usr/local/sbin/volante-deploy" > "$SUDOERS"
if visudo -cf "$SUDOERS" >/dev/null; then
  run install -o root -g root -m 0440 "$SUDOERS" /etc/sudoers.d/volante-ci
else rm -f "$SUDOERS"; echo "sudoers rule failed visudo -c: not installed" >&2; exit 1; fi
rm -f "$SUDOERS"

echo "done. Check: the runner shows Idle in the repo's Settings > Actions > Runners; 'sudo -u $CI_USER sudo -n /usr/local/sbin/volante-deploy --dry-run' prints usage."
