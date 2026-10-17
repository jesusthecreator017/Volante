#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
wasm-pack build --target web --out-dir web/pkg --release
# Assets are fetched at runtime from web/assets
[ -e web/assets ] || ln -s ../assets web/assets
echo "Serve with: python3 -m http.server -d web 8080"
