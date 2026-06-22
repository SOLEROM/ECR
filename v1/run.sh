#!/usr/bin/env bash
#
# run.sh - Spin up the ECR app.
#
# Creates a local .venv (if missing), installs dependencies from
# requirements.txt, then launches app.py. Any extra arguments are
# forwarded to app.py (e.g. ./run.sh --port 8080).
#
set -euo pipefail

# Resolve the directory this script lives in, so it works from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
PYTHON="${PYTHON:-python3}"

# Create the virtualenv on first run.
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtualenv in $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Use the venv's interpreter directly (no need to 'activate').
VENV_PY="$VENV_DIR/bin/python"

echo "==> Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip >/dev/null

echo "==> Installing dependencies from requirements.txt"
"$VENV_PY" -m pip install -r requirements.txt

echo "==> Starting ECR (app.py)"
exec "$VENV_PY" app.py "$@"
