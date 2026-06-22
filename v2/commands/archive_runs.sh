#!/usr/bin/env bash
# Housekeeping — runs on the BASE STATION (not a fleet node).
# ccFleet exports: CCFLET_RUNS_DIR  CCFLET_FLEET  CCFLET_VARIANT  CCFLET_ALGO  CCFLET_NODES
# This demo only REPORTS what it would archive; edit it from the Config page to make
# it actually move files. Nothing here is destructive as shipped.
set -eu
RUNS="${CCFLET_RUNS_DIR:-runs}"
echo "[archive_runs] base-station housekeeping"
echo "  runs dir : $RUNS"
echo "  fleet    : ${CCFLET_FLEET:-?}  variant=${CCFLET_VARIANT:-?}  algo=${CCFLET_ALGO:-?}"
old=$(find "$RUNS" -maxdepth 1 -type d -name '20*' -mtime +7 2>/dev/null | wc -l | tr -d ' ')
echo "  sessions older than 7 days: ${old:-0}  (report only — edit me to archive them)"
echo "[archive_runs] done"
