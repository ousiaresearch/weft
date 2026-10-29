#!/usr/bin/env bash
set -euo pipefail

# Build release artifacts for Weft alpha.
# Requires: python3.11+, pip, build

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

# Prefer an explicit Python >= 3.11; fail clearly if none is available.
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

PY_VER="$("${PYTHON_BIN}" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
PY_MAJOR="$("${PYTHON_BIN}" -c 'import sys; print(sys.version_info.major)')"
PY_MINOR="$("${PYTHON_BIN}" -c 'import sys; print(sys.version_info.minor)')"
if [ "${PY_MAJOR}" -lt 3 ] || [ "${PY_MAJOR}" -eq 3 -a "${PY_MINOR}" -lt 11 ]; then
  echo "Error: ${PYTHON_BIN} is ${PY_VER}, but Weft requires Python >= 3.11" >&2
  exit 1
fi

echo "==> Using ${PYTHON_BIN} (${PY_VER})"

echo "==> Cleaning previous builds"
rm -rf build/ dist/ *.egg-info

echo "==> Installing build dependencies"
if ! "${PYTHON_BIN}" -m pip install --quiet build 2>/dev/null; then
  echo "Warning: pip install build failed; trying with --break-system-packages" >&2
  if ! "${PYTHON_BIN}" -m pip install --quiet --break-system-packages build 2>/dev/null; then
    echo "Warning: could not install build package; attempting build anyway" >&2
  fi
fi

echo "==> Building wheel"
"${PYTHON_BIN}" -m build --wheel

echo "==> Verifying wheel"
ls -lh dist/*.whl

echo "==> Installing wheel in fresh venv"
VERIFY_VENV="$(mktemp -d -t weft-alpha-verify-XXXXXXXXXX)"
umask 0077
"${PYTHON_BIN}" -m venv "${VERIFY_VENV}"
"${VERIFY_VENV}/bin/python" -m pip install --quiet dist/*.whl

echo "==> Checking entrypoint"
"${VERIFY_VENV}/bin/weft" --help >/dev/null
"${VERIFY_VENV}/bin/weft" --db /tmp/weft-alpha-test.db serve --help >/dev/null

echo "==> Done. Artifacts in dist/"