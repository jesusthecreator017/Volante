#!/usr/bin/env bash
# Runs on your laptop: builds the web app and ships it with the server to the Pi, then installs/updates there.
#   deploy/pi/deploy.sh pi@raspberrypi.local
set -euo pipefail
TARGET="${1:?usage: deploy/pi/deploy.sh user@host}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

(cd "$ROOT/app" && npm ci && npm test && npm run build)
mkdir -p "$STAGE/app"
cp -R "$ROOT/app/dist" "$ROOT/app/server" "$ROOT/app/package.json" "$STAGE/app/"
rm -rf "$STAGE/app/server/data" # sessions live in /var/lib/oso on the Pi, never overwritten by a deploy
cp "$ROOT/deploy/pi/oso.service" "$ROOT/deploy/pi/install.sh" "$STAGE/"

rsync -az --delete "$STAGE/" "$TARGET:oso-deploy/"
ssh -t "$TARGET" 'bash oso-deploy/install.sh "$HOME/oso-deploy"'
