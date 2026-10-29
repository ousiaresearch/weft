#!/usr/bin/env bash
set -euo pipefail

# Build release artifacts for Weft alpha.
# Requires: python3.11+, pip, build

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

echo "==> Cleaning previous builds"
rm -rf build/ dist/ *.egg-info

echo "==> Installing build dependencies"
python3 -m pip install --quiet build

echo "==> Building wheel"
python3 -m build --wheel

echo "==> Verifying wheel"
ls -lh dist/*.whl

echo "==> Done. Artifacts in dist/"