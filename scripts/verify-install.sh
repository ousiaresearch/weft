#!/usr/bin/env bash
set -euo pipefail

# verify-install.sh — fresh-venv install, smoke test, and uninstall for Weft alpha.
# Requires: python3.11+, pip, wheel in dist/

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

if [ ! -d "dist" ] || [ -z "$(ls dist/*.whl 2>/dev/null)" ]; then
  echo "Error: no wheel found in dist/; run scripts/build-release.sh first" >&2
  exit 1
fi

WHEEL="$(ls dist/*.whl | head -n 1)"
VENV="/tmp/weft-install-verify"

# Prefer an explicit Python >= 3.11.
if [ -n "${WEFT_PYTHON:-}" ]; then
  PYTHON_BIN="${WEFT_PYTHON}"
else
  for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      PYTHON_BIN="${candidate}"
      break
    fi
  done
fi

if [ -z "${PYTHON_BIN:-}" ]; then
  echo "Error: no Python interpreter found" >&2
  exit 1
fi

rm -rf "${VENV}"
"${PYTHON_BIN}" -m venv "${VENV}"
"${VENV}/bin/python" -m pip install --quiet "${WHEEL}"

echo "==> Checking entrypoint"
"${VENV}/bin/weft" --help >/dev/null
"${VENV}/bin/weft" --db /tmp/weft-install-test.db serve --help >/dev/null

echo "==> Uninstalling"
"${VENV}/bin/python" -m pip uninstall -y weft-memory >/dev/null

echo "==> Verifying uninstall"
if [ -x "${VENV}/bin/weft" ]; then
  echo "Error: weft entrypoint still exists after uninstall" >&2
  exit 1
fi
if "${VENV}/bin/python" -c "import sys; sys.path = [p for p in sys.path if p]; import weft" 2>/dev/null; then
  echo "Error: weft package still importable after uninstall" >&2
  exit 1
fi

echo "==> Done. Install/uninstall cycle verified for ${WHEEL}"
