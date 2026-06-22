#!/usr/bin/env bash
#
# run.sh — spin up the ccflet fleet Command & Control web app.
#
# Bootstraps everything the first time and on every run after:
#   * creates the project venv at .venv (in this project root) if it is missing
#     (system Python is PEP 668 externally-managed — we never install into it),
#   * installs / updates deps from requirements.txt only when needed
#     (venv just made, requirements.txt changed, or an import is missing),
#   * then launches the server, forwarding any args to app.py.
#
# Usage:
#   ./run.sh                 # live fleet  (edit fleet/fleet.yaml first)
#   ./run.sh --mock          # simulated fleet, no hardware  → http://127.0.0.1:5000
#   ./run.sh --mock --port 5057 --host 0.0.0.0
#   ./run.sh --dry-run       # print remote commands, run nothing
#   ./run.sh --reinstall ... # force a clean dependency (re)install, then run
#   ./run.sh --help          # app.py's own flags
#
set -euo pipefail

# --- locate ourselves (work regardless of caller's cwd) ----------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
REQ="$ROOT/requirements.txt"
APP="$ROOT/app.py"
STAMP="$VENV/.deps-installed"
PY="$VENV/bin/python"

say() { printf '\033[36m[run]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31m[run] error:\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$REQ" ] || die "requirements not found at $REQ"
[ -f "$APP" ] || die "app not found at $APP"

# --- optional: force a reinstall ---------------------------------------------
REINSTALL=0
if [ "${1:-}" = "--reinstall" ]; then REINSTALL=1; shift; fi

# --- ensure the venv exists --------------------------------------------------
if [ ! -x "$PY" ]; then
  BASE_PY="$(command -v python3 || command -v python || true)"
  [ -n "$BASE_PY" ] || die "no python3 found on PATH — install Python 3."
  say "creating venv at .venv ($("$BASE_PY" --version 2>&1)) …"
  "$BASE_PY" -m venv "$VENV" 2>/dev/null \
    || die "could not create venv — on Debian/Ubuntu try: sudo apt install python3-venv"
fi

# --- decide whether deps need (re)installing ---------------------------------
need_install=$REINSTALL
[ -f "$STAMP" ] || need_install=1                 # never installed
[ "$REQ" -nt "$STAMP" ] && need_install=1          # requirements.txt changed
if [ "$need_install" -eq 0 ]; then                 # half-built venv / missing import?
  "$PY" - <<'PYEOF' >/dev/null 2>&1 || need_install=1
import flask, flask_socketio, socketio, simple_websocket, paramiko, yaml, markdown  # noqa
PYEOF
fi

if [ "$need_install" -ne 0 ]; then
  say "installing dependencies from requirements.txt …"
  "$PY" -m pip install --upgrade --quiet --disable-pip-version-check pip \
    || die "pip self-upgrade failed"
  "$PY" -m pip install --quiet --disable-pip-version-check -r "$REQ" \
    || die "dependency install failed"
  touch "$STAMP"
  say "dependencies ready."
fi

# --- launch ------------------------------------------------------------------
say "starting ccflet — Fleet Command & Control"
exec "$PY" "$APP" "$@"
