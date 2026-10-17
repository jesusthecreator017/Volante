#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
wasm-pack build crates/oso-arena --target web --out-dir ../../web/arena/pkg --out-name oso --release
# Assets are fetched at runtime from web/arena/assets
[ -e web/arena/assets ] || ln -s ../../assets web/arena/assets
echo "Serve with: python3 -m http.server -d web/arena 8080"
