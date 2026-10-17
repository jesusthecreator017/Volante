#!/usr/bin/env bash
# Builds the Pi handoff bundle: dist/oso-arena-pi/ and dist/oso-arena-pi.zip (dist/ is gitignored).
#   arena/handoff/make_bundle.sh
# Needs: tools/.venv/bin/python (or $PYTHON) with numpy, safetensors and `build`; Rust + wasm-pack (the viewer); Node + npm (the site).
# Everything is built from the checkout this script lives in. A dirty tree is allowed but stamped "-dirty" in PROMPT.md.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD
PY=${PYTHON:-tools/.venv/bin/python}
OUT=dist/oso-arena-pi
HANDOFF=arena/handoff
export PATH="/opt/homebrew/opt/rustup/bin:$PATH"   # rustup's cargo on a Homebrew Mac; harmless elsewhere

die() { echo "make_bundle: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

step "prerequisites"
missing=()
[ -x "$PY" ] || command -v "$PY" >/dev/null 2>&1 || missing+=("python: $PY not found (create tools/.venv, or set PYTHON=/path/to/python)")
if [ ${#missing[@]} -eq 0 ]; then
  "$PY" -c "import numpy, safetensors" 2>/dev/null || missing+=("python packages numpy and safetensors (pip install numpy safetensors)")
  "$PY" -c "import build" 2>/dev/null || missing+=("python package build (pip install build) for the kit wheel")
fi
command -v cargo >/dev/null 2>&1 || missing+=("cargo (Rust): https://rustup.rs")
command -v wasm-pack >/dev/null 2>&1 || missing+=("wasm-pack (the viewer's wasm build): brew install wasm-pack, or cargo install wasm-pack")
command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 || missing+=("node and npm (the site build)")
command -v zip >/dev/null 2>&1 || missing+=("zip")
if [ ${#missing[@]} -gt 0 ]; then
  printf 'make_bundle: missing prerequisites:\n' >&2
  printf '  - %s\n' "${missing[@]}" >&2
  exit 1
fi

KIT_VERSION=$(PYTHONPATH=arena/kit "$PY" -c "from oso_arena import KIT_VERSION; print(KIT_VERSION)")
CONTRACT=$(PYTHONPATH=arena/kit "$PY" -c "from oso_arena import CONTRACT; print(CONTRACT)")
GIT_COMMIT=$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
if [ "$GIT_COMMIT" != unknown ] && [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null; git status --porcelain --untracked-files=all -- arena/handoff 2>/dev/null)" ]; then GIT_COMMIT="$GIT_COMMIT-dirty"; fi
echo "kit $KIT_VERSION, contract $CONTRACT, commit $GIT_COMMIT"

rm -rf "$OUT" "$OUT.zip"
mkdir -p "$OUT/contract" "$OUT/kit" "$OUT/docs" "$OUT/smoke"

step "contract"
cp arena/contract/CONTRACT.md arena/contract/header.schema.json "$OUT/contract/"

step "kit wheel (python -m build)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir "$TMP/kit" "$TMP/wheel"
tar -C arena/kit --exclude='__pycache__' --exclude='dist' --exclude='build' --exclude='*.egg-info' --exclude='runs' -cf - . | tar -C "$TMP/kit" -xf -
# --no-isolation: the build backend (setuptools) must be in the venv; the same way arena/kit/tests/test_package.py builds it
"$PY" -m build --no-isolation --wheel --outdir "$TMP/wheel" "$TMP/kit" >"$TMP/build.log" 2>&1 || { cat "$TMP/build.log" >&2; die "wheel build failed"; }
WHEEL=$(ls "$TMP/wheel"/oso_arena-*.whl)
cp "$WHEEL" "$OUT/kit/"
echo "$(basename "$WHEEL")"

step "base model"
BASE=arena/kit/oso_arena/data/base_40min.safetensors
if [ -f "$BASE" ]; then
  cp "$BASE" "$OUT/base_40min.safetensors"
  BASE_SHA=$("$PY" -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$BASE")
  BASE_STATUS="included as base_40min.safetensors (sha256 $BASE_SHA)"
  echo "$BASE_STATUS"
else
  cat > "$OUT/MISSING_BASE_MODEL.txt" <<NOTE
base_40min.safetensors was not in this build (arena/kit/oso_arena/data/ had no base model when the bundle was made).
Do not invent one. Build everything that does not need it (the site, the API, uploads, the check, matches between uploaded
models and the scripted bot, replays) and treat the house player "House Base" and the "identical to the base model" upload
rule as TODO until the owner sends base_40min.safetensors. When it arrives it is a normal oso-arena/1 file: put it on the
ladder as the house player "House Base" (all-6 build) and reject uploads with its sha256.
NOTE
  BASE_STATUS="NOT INCLUDED: see MISSING_BASE_MODEL.txt (the owner will send base_40min.safetensors; the House Base player and the base-hash upload rule wait for it)"
  echo "missing: wrote MISSING_BASE_MODEL.txt"
fi

step "viewer (web/arena/build.sh)"
web/arena/build.sh >"$TMP/viewer.log" 2>&1 || { tail -40 "$TMP/viewer.log" >&2; die "viewer build failed"; }
[ -f web/dist/viewer/index.html ] || die "web/dist/viewer/index.html missing after the viewer build"
cp -R web/dist/viewer "$OUT/viewer"

step "site (npm run build -> site/dist)"
(
  cd site
  [ -d node_modules ] || npm ci --no-audit --no-fund
  npm run build
) >"$TMP/site.log" 2>&1 || { tail -40 "$TMP/site.log" >&2; die "site build failed"; }
[ -f site/dist/index.html ] || die "site/dist/index.html missing after the site build"
# the mock-API code is compiled out of the normal build; refuse a dist that still has it
if grep -rl "mock API installed" site/dist >/dev/null 2>&1; then die "site/dist contains the mock API: run a normal 'npm run build', not --mode mock"; fi
cp -R site/dist "$OUT/site"

step "docs the brief points to"
cp site/README.md "$OUT/docs/site-README.md"
cp web/arena/README.md "$OUT/docs/viewer-README.md"
cp deploy/pi/README.md "$OUT/docs/lan-replay-server-README.md"
cp arena/kit/README.md "$OUT/docs/kit-README.md"
cp site/src/api.ts "$OUT/docs/site-api.ts"
cp site/src/types.ts "$OUT/docs/site-types.ts"
cp site/src/mock.ts "$OUT/docs/site-mock-fixtures.ts"

step "smoke files"
"$PY" "$HANDOFF/make_smoke.py" "$OUT/smoke" --verify || die "a smoke file did not give its expected check result"

step "PROMPT.md"
KIT_VERSION=$KIT_VERSION CONTRACT=$CONTRACT GIT_COMMIT=$GIT_COMMIT BASE_STATUS=$BASE_STATUS WHEEL_NAME=$(basename "$WHEEL") \
  "$PY" - "$HANDOFF/PROMPT.md" "$OUT/PROMPT.md" <<'PYEOF'
import os, re, sys
src, dst = sys.argv[1:3]
text = open(src, encoding="utf-8").read()
vals = {"KIT_VERSION": os.environ["KIT_VERSION"], "CONTRACT": os.environ["CONTRACT"], "GIT_COMMIT": os.environ["GIT_COMMIT"],
        "BASE_MODEL": os.environ["BASE_STATUS"], "WHEEL": os.environ["WHEEL_NAME"]}
text = re.sub(r"\{\{(\w+)\}\}", lambda m: vals[m.group(1)] if m.group(1) in vals else sys.exit("unknown placeholder " + m.group(0)), text)
open(dst, "w", encoding="utf-8").write(text)
PYEOF
grep -q '{{' "$OUT/PROMPT.md" && die "unfilled placeholder in PROMPT.md"

step "checksums + zip"
(cd "$OUT" && find . -type f ! -name SHA256SUMS | sort | xargs shasum -a 256 > SHA256SUMS)
(cd dist && zip -qrX oso-arena-pi.zip oso-arena-pi)

step "bundle"
(cd dist && find oso-arena-pi -type f | sort | while read -r f; do printf '%10d  %s\n' "$(wc -c < "$f")" "$f"; done | grep -v '^ *[0-9]*  oso-arena-pi/\(site\|viewer\)/.*' || true)
echo "  site/ and viewer/: $(find "$OUT/site" -type f | wc -l | tr -d ' ') and $(find "$OUT/viewer" -type f | wc -l | tr -d ' ') files, $(du -sk "$OUT/site" | cut -f1) KiB and $(du -sk "$OUT/viewer" | cut -f1) KiB"
ls -l dist/oso-arena-pi.zip
echo "done: $OUT and $OUT.zip"
