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

echo "==> Installing wheel in fresh venv"
python3 -m venv /tmp/weft-alpha-verify
/tmp/weft-alpha-verify/bin/python -m pip install --quiet dist/*.whl

echo "==> Checking entrypoint"
/tmp/weft-alpha-verify/bin/weft --help >/dev/null
/tmp/weft-alpha-verify/bin/weft --db /tmp/weft-alpha-test.db serve --help >/dev/null

echo "==> Done. Artifacts in dist/"