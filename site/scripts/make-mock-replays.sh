#!/usr/bin/env bash
# Short real matches (kit) for `npm run dev:mock` -> site/.mock-replays/. Python needs numpy + safetensors.
# PY=path/to/python picks the interpreter; OSO_MODEL_A / OSO_MODEL_B pick the players (.safetensors path, "base" or "bot").
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${PY:-python3}" scripts/make-mock-replays.py
