#!/usr/bin/env bash
# compile.sh — the ccFleet Compiler entry point (wish list → working app).
#
#   ./compile.sh new <name>                 fork the template into apps/<name>/
#   ./compile.sh --app apps/<name> --from dream --to app      full build
#   ./compile.sh --app apps/<name> --from subparts --to app   rebuild app only
#   ./compile.sh --app apps/<name> --only params              redraft params, then stop
#   ./compile.sh --app apps/<name> scaffold gate-c            dump a part default to edit
#   ./compile.sh --app apps/<name> check                      manifest drift check
#   ./compile.sh --app apps/<name> status                     stage statuses
#
# Uses the project venv if present (deps live there, PEP 668), else system python3.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="$here/.venv/bin/python"
[ -x "$py" ] || py="python3"
exec "$py" -m compiler "$@"
