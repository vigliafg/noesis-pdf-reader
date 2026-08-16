#!/usr/bin/env bash
# Build a Noesis PDF Reader release bundle with PyInstaller (Linux / macOS).
#
# Usage:
#   packaging/build.sh [light|medium]
#
# Creates a fresh venv (./.venv-build), installs the right dependencies
# (CPU-only torch for the "medium" variant) and produces dist/NoesisPDFReader.
# The "full" tier (CUDA) is source-only: see requirements-cuda.txt.

set -euo pipefail

VARIANT="${1:-light}"
case "$VARIANT" in
  light|medium) ;;
  *) echo "usage: $0 [light|medium]" >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv-build"

echo "==> creating venv ($VENV)"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip

echo "==> installing light dependencies"
"$VENV/bin/pip" install -r requirements.txt

if [ "$VARIANT" = "medium" ]; then
  echo "==> installing CPU-only torch + docling"
  "$VENV/bin/pip" install torch --index-url https://download.pytorch.org/whl/cpu
  "$VENV/bin/pip" install -r requirements-docling.txt
fi

echo "==> installing PyInstaller"
"$VENV/bin/pip" install "pyinstaller>=6.16"

echo "==> building ($VARIANT)"
if [ "$VARIANT" = "medium" ]; then
  NOESIS_DOCLING=1 "$VENV/bin/pyinstaller" --clean --noconfirm packaging/noesis.spec
else
  NOESIS_DOCLING=0 "$VENV/bin/pyinstaller" --clean --noconfirm packaging/noesis.spec
fi

echo "==> done: dist/NoesisPDFReader"
