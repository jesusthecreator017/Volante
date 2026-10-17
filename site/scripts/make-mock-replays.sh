#!/usr/bin/env bash
# Short real matches (kit) for `npm run dev:mock` -> site/.mock-replays/. Python needs numpy + safetensors.
# PY=path/to/python picks the interpreter; OSO_MODEL_A / OSO_MODEL_B pick the players (.safetensors path, "base" or "bot").
set -euo pipefail
PY=${PY:-python3}
case "$PY" in */*) PY=$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY") ;; esac # a relative PY= is relative to where you ran this
cd "$(dirname "$0")/.."
exec "$PY" scripts/make-mock-replays.py
