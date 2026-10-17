#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
wasm-pack build crates/oso-arena --target web --out-dir ../../web/arena/pkg --out-name oso --release
# Assets are fetched at runtime from web/arena/assets
[ -e web/arena/assets ] || ln -s ../../assets web/arena/assets

# Self-contained bundle the Pi serves at /viewer/ (real copies, no symlinks, no bundled replay)
D=web/dist/viewer
rm -rf "$D"
mkdir -p "$D/pkg" "$D/assets"
cp web/arena/index.html web/arena/arena.js web/arena/arena.css web/arena/timeline.js "$D/"
cp web/arena/pkg/oso.js web/arena/pkg/oso_bg.wasm "$D/pkg/"
[ -d web/arena/pkg/snippets ] && cp -R web/arena/pkg/snippets "$D/pkg/"
cp -R assets/calib assets/models "$D/assets/"

echo "Serve with: python3 -m http.server -d web/arena 8080"
echo "  then open http://localhost:8080/?match=assets/replay/replay.bin (or drop a match.bin)"
echo "Pi bundle: web/dist/viewer/ (serve at /viewer/, ?match=/api/matches/<id>/replay.bin)"
