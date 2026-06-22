---
noteId: "43079d806b3411f1b060577f73b9a94a"
tags: []

---

# `scripts/` — base-station helper scripts

Small, standalone shell utilities for the operator/dev to run **from a terminal**
on the machine hosting `ccflet` (the base station). Housekeeping that does not
belong in the per-run loop.

> **Not** the operator command catalog. The buttons on the dashboard / Config
> page come from `commands/commands_{host,roleA,roleB}.yaml` (+ `commands/*.sh`) and
> are validated, hot-reloaded, and audited (config over code). Scripts here are plain
> command-line tools — nothing in `ccflet` runs them automatically.

Run them from the `ccflet/` directory (or anywhere — each resolves its own paths):

```bash
scripts/clear_sessions.sh -n        # dry-run first
scripts/clear_sessions.sh           # then for real
```

## Scripts

| Script | What it does |
|---|---|
| `clear_sessions.sh` | **Deletes ALL ops sessions** — every session dir and every `*.zip` export under `runs/`, so the Sessions page goes back to "no sessions yet". |

### `clear_sessions.sh`

Wipes every session directory (those holding a `manifest.json`) and every
exported `*.zip` under the runs dir.

```bash
scripts/clear_sessions.sh                  # confirm, then delete all
scripts/clear_sessions.sh -y               # skip the confirmation prompt
scripts/clear_sessions.sh -n | --dry-run   # list what would be deleted
scripts/clear_sessions.sh /path/to/runs    # target a specific runs dir
CCFLET_RUNS_DIR=/path scripts/clear_sessions.sh
```

The runs dir is resolved as: positional arg → `$CCFLET_RUNS_DIR` → `../runs`
next to the script. By default it asks for confirmation; pass `-y` to skip.

> **DESTRUCTIVE and IRREVERSIBLE.** Run it only while `ccflet` is **stopped**
> (or with no run active) — deleting a live session dir out from under a
> running server can corrupt the open session. Export anything worth keeping
> (the per-row **ZIP** on `/sessions`) first.

## Adding a script

Drop a `*.sh` in here, `chmod +x` it, and add a row to the table above. Keep it
self-contained: resolve `runs/`/`fleet/` relative to the script, prefer a
`-n`/`--dry-run` preview for anything destructive, and exit non-zero on failure.
