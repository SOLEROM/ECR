---
title: "Logs view (base-station log windows)"
order: 15
summary: "The dashboard's third view shows a grid of live tail -F windows over operator-defined base-station log files (logs/logs.yaml), and snapshots each into the session ZIP on export."

---

# Logs view — base-station log windows

## What it is

The dashboard's view toggle has a third option beside **Grid** and **Tabs**: **Logs**.
It shows a responsive grid of **live log windows**, each an xterm pane tailing a file
**on the base station** (the machine running `ccFleet`) with `tail -F`. It is the
read-only, many-panes-at-once counterpart of a node's "Live logs" tab — but for the
control station's *own* processes (a daemon's log, a service's stdout, the system log),
not a fleet node.

These are **not** fleet nodes (principle **P2**): `ccFleet` never tails the nodes' own
data here. The per-node live-log tab (over SSH) is the node-side feature — see
[08 Connectivity & Live Streaming](08-connectivity-and-streaming.md). This view is the
base-station-local sibling.

## Where the windows live (config over code, P8)

Everything is operator-editable from the **Config** page under the **Logs** root
(`logs/logs.yaml`) — no source change. Each entry is one window:

```yaml
logs:
  default_lines: 200        # trailing lines a window seeds with unless it sets `lines`
  windows:
    - key: ccfleet          # unique bare token — also the artifact filename
      label: ccFleet server # pane title (defaults to `process`, then `key`)
      process: app.py       # the process being watched (shown in the header)
      path: /tmp/ccflet/ccfleet.log   # the FILE to tail on the base station
      hint: the ccFleet base-station server log
    - key: syslog
      label: System log
      process: rsyslogd
      path: /var/log/syslog
      lines: 300            # per-window override of default_lines
```

A save is **validated** (`key` a bare token + unique, `path` a non-empty single path,
`lines` a positive int), **backed up**, **hot-reloaded** (the Logs view rebuilds its
panes via a `logs_changed` push) and **audited** — exactly like fleet/profile/command/
state edits.

## How it works

```
logs/logs.yaml ──► core/logs.py (LogWindow model) ──► core/logs.LogsRegistry
                                                            (one ordered window list)
                                                                  │
                                              core/log_stream.py (I/O shell)
   subscribe_logwin {key} ──► start tail -F <path> (argv list, no shell)
   each line ──► "logwin_line" {key,line} ──► the window's xterm pane
                                          └─► appended to the session logs/ dir
```

`GET /api/logs` lists the configured windows; the Logs view builds one pane per entry
client-side (so editing the YAML + reload changes the view with no template edit). Panes
subscribe (`subscribe_logwin`) only while the Logs view is showing and unsubscribe on
leave, so the base station only tails what's on screen; the number of concurrent tails is
capped. `path` is an operator-config value passed to `tail` as an **argv element** (never
a shell), so there is no injection surface.

Two per-browser layout/runtime affordances on each pane (no server state):

- **Drag to reorder** — drag a pane by its `⠿` grip to arrange the wall; the order is
  saved in `localStorage` (`ccflet_logwin_order`, keyed by window key) and re-applied on
  every (re)build. Streaming is keyed by window key, not DOM order, so reordering never
  touches a subscription — the same model as the grid's card drag.
- **Start / Stop** — every window starts **live**; the header's Stop button unsubscribes
  just that window (the base station drops its tail) and Start re-subscribes (the pane
  re-seeds with the latest `tail -n` lines). State is per-page — a reload returns every
  window to the running default.

## Saved into the session ZIP (P6)

On **session export** (`/sessions/<sid>/export`), `ccFleet` snapshots **every configured
window** — whether or not a pane was ever opened — into `artifacts/logs/<key>.log` (a
`tail -n <lines>` capture with a small provenance header), then builds the ZIP. So a run
always carries the logs the operator chose to watch, and the capture is recorded in that
session's own audit log. Lines viewed live are *also* persisted into the session `logs/`
dir as they stream, like the per-node tails.

## Trust model

Tailing reads a file **on the base station** as the app user — the same deliberate
config-over-code posture as local custom commands and cmd States (closed LAN, trusted
operators, full audit), not an oversight. `path` stays a single bare path and is passed
as an argv element, so a typo is a line-numbered validation error, not a broken shell.

## Mock / dry-run

Under `--mock` and `--dry-run` **no file is read** — each window streams rolling
placeholder lines (so the view is visibly alive without touching the base station), and
the export snapshot writes a "simulated run" note instead of file contents. Tailing is
held **disabled** when base-station local exec is off (`--no-local-commands`) — the pane
shows a notice and the snapshot records that capture was skipped, since it executes on the
base station (the higher-blast-radius path).

> If a configured `path` doesn't exist yet, `tail -F` waits for it and the pane shows
> tail's own "cannot open … waiting" line — a safe degradation, and the window starts
> streaming as soon as the file appears.
