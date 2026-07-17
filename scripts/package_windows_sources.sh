#!/usr/bin/env bash
# Crée release/mne_v3_project-windows-build.zip (sources pour compilation Windows).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/release/mne_v3_project-windows-build.zip"
mkdir -p "$ROOT/release"
rm -f "$OUT"
cd "$ROOT"
zip -r "$OUT" . \
  -x ".git/*" \
  -x "*__pycache__/*" \
  -x "*.pyc" \
  -x ".venv/*" \
  -x "venv/*" \
  -x "dist/*" \
  -x "build/*" \
  -x "*.sqlite3" \
  -x "*.sqlite" \
  -x "*.db" \
  -x ".DS_Store" \
  -x "*/.DS_Store" \
  -x "*egg-info/*" \
  -x "* 2/*" \
  -x "* 2.*" \
  -x "release/mne_v3_project-windows-build.zip"
echo "Archive créée : $OUT ($(du -h "$OUT" | cut -f1))"
