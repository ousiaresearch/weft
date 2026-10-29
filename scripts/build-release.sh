#!/usr/bin/env bash
set -euo pipefail

# Build release artifacts for Weft alpha.
# Requires: python3.11+, pip, build

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

# Prefer a Python >= 3.11 explicitly; fall back to python3 if only one is installed.
PYTHON_BIN="${WEFT_PYTHON:-python3.11}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

echo "==> Cleaning previous builds"
rm -rf build/ dist/ *.egg-info

echo "==> Installing build dependencies"
"${PYTHON_BIN}" -m pip install --quiet build

echo "==> Building wheel"
"${PYTHON_BIN}" -m build --wheel

echo "==> Verifying wheel"
ls -lh dist/*.whl

echo "==> Installing wheel in fresh venv"
rm -rf /tmp/weft-alpha-verify
"${PYTHON_BIN}" -m venv /tmp/weft-alpha-verify
/tmp/weft-alpha-verify/bin/python -m pip install --quiet dist/*.whl

echo "==> Checking entrypoint"
/tmp/weft-alpha-verify/bin/weft --help >/dev/null
/tmp/weft-alpha-verify/bin/weft --db /tmp/weft-alpha-test.db serve --help >/dev/null

echo "==> Done. Artifacts in dist/"