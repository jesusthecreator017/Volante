#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
wasm-pack build --target web --out-dir web/pkg --release
# Assets are fetched at runtime from web/assets
[ -e web/assets ] || ln -s ../assets web/assets

# Self-contained arena viewer bundle (real copies, no symlinks)
D=web/dist/viewer
rm -rf "$D"
mkdir -p "$D/pkg" "$D/assets"
if [ -d web/arena ]; then
  cp web/arena/index.html web/arena/arena.js web/arena/arena.css "$D/"
else
  echo "warning: web/arena/ missing; skipping arena files" >&2
fi
cp web/viewer.css web/timeline.js web/stats.js "$D/"
cp web/pkg/oso.js web/pkg/oso_bg.wasm "$D/pkg/"
[ -d web/pkg/snippets ] && cp -R web/pkg/snippets "$D/pkg/"
cp -R assets/calib "$D/assets/"
cp -R assets/models "$D/assets/"

echo "Serve the standalone viewer with: python3 -m http.server -d web 8080"
echo "Serve the arena bundle with:      python3 -m http.server -d web/dist 8080"
echo "  then open http://localhost:8080/viewer/index.html?match=<url>"
