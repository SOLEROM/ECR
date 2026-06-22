#!/usr/bin/env bash
# ccflet helper — clear & delete ALL ops sessions under runs/.
#
# Wipes every session directory (manifest.json + events.jsonl + logs/ +
# artifacts/) and every exported *.zip under the runs/ dir. Afterwards the
# Sessions page (/sessions) shows "no sessions yet" on the next refresh.
#
#   DESTRUCTIVE and IRREVERSIBLE. There is no undo and no confirm in the web UI.
#   Run it only while ccflet is STOPPED (or at least with no run active) —
#   deleting a live session dir out from under a running server can corrupt the
#   open session. Export anything you need (the per-row ZIP) first.
#
# Usage:
#   scripts/clear_sessions.sh                  # confirm, then delete all
#   scripts/clear_sessions.sh -y               # skip the confirmation prompt
#   scripts/clear_sessions.sh -n               # dry-run: list what would go
#   scripts/clear_sessions.sh /path/to/runs    # target a specific runs dir
#   CCFLET_RUNS_DIR=/path scripts/clear_sessions.sh
#
set -euo pipefail

prog="clear_sessions"
assume_yes=0
dry_run=0
runs=""

usage() {
  sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     assume_yes=1 ;;
    -n|--dry-run) dry_run=1 ;;
    -h|--help)    usage; exit 0 ;;
    --)           shift; [ $# -gt 0 ] && runs="$1"; break ;;
    -*)           echo "[$prog] unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)            runs="$1" ;;
  esac
  shift
done

# Resolve the runs dir: positional arg > $CCFLET_RUNS_DIR > ../runs next to this script.
[ -n "$runs" ] || runs="${CCFLET_RUNS_DIR:-}"
if [ -z "$runs" ]; then
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  runs="$(cd "$here/.." && pwd)/runs"
fi

if [ ! -d "$runs" ]; then
  echo "[$prog] runs dir not found: $runs" >&2
  exit 1
fi

# A session dir is any directory under runs/ that holds a manifest.json; exports
# are the sibling *.zip files. Matching on manifest.json keeps us from nuking
# anything that is not actually a ccflet session.
dirs=()
zips=()
while IFS= read -r d; do dirs+=("$d"); done \
  < <(find "$runs" -mindepth 1 -maxdepth 1 -type d -exec test -e '{}/manifest.json' ';' -print | sort)
while IFS= read -r z; do zips+=("$z"); done \
  < <(find "$runs" -mindepth 1 -maxdepth 1 -type f -name '*.zip' | sort)

count=$(( ${#dirs[@]} + ${#zips[@]} ))
echo "[$prog] runs dir : $runs"
echo "[$prog] found    : ${#dirs[@]} session dir(s), ${#zips[@]} zip(s)"

if [ "$count" -eq 0 ]; then
  echo "[$prog] nothing to delete — already clear"
  exit 0
fi

if [ "$dry_run" -eq 1 ]; then
  for d in "${dirs[@]}"; do echo "  would delete  $d"; done
  for z in "${zips[@]}"; do echo "  would delete  $z"; done
  echo "[$prog] dry-run — nothing deleted"
  exit 0
fi

if [ "$assume_yes" -ne 1 ]; then
  printf "[%s] delete ALL %d item(s) under %s? [y/N] " "$prog" "$count" "$runs"
  read -r reply || reply=""
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "[$prog] aborted — nothing deleted"; exit 1 ;;
  esac
fi

[ "${#dirs[@]}" -gt 0 ] && for d in "${dirs[@]}"; do rm -rf -- "$d"; done
[ "${#zips[@]}" -gt 0 ] && for z in "${zips[@]}"; do rm -f  -- "$z"; done

echo "[$prog] deleted ${#dirs[@]} session(s) and ${#zips[@]} zip(s)"
echo "[$prog] the Sessions page will show 'no sessions yet' on refresh"
