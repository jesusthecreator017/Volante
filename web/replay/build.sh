#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
wasm-pack build crates/oso-replay --target web --out-dir ../../web/replay/pkg --out-name oso --release
# Assets are fetched at runtime from web/replay/assets
[ -e web/replay/assets ] || ln -s ../../assets web/replay/assets
echo "Serve with: python3 -m http.server -d web/replay 8080"
