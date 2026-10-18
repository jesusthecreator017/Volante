#!/bin/sh
# Copies the stroke tree models from models/strokes/ into the OsoKit bundle resources.
# Run after tools/strokes/export_trees.py or train.py --final; CI fails (test_export_trees.py, swift test) when the copies drift.
set -eu
root="$(cd "$(dirname "$0")/../.." && pwd)"
dst="$root/apple/OsoKit/Sources/OsoKit/Resources"
mkdir -p "$dst"
for f in stroke_group_v0.trees.json stroke_class_v0.trees.json; do
  cp "$root/models/strokes/$f" "$dst/$f"
done
