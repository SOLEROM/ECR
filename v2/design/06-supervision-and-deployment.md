---
noteId: "565c75d06b0311f1b060577f73b9a94a"
tags: []
title: "Supervision & Deployment"
order: 6
summary: "How daemons are started/stopped/checked (pidfile or systemd) and how code is shipped to nodes."

---

# Supervision & Deployment

## Overview

Two concerns sit between "the orchestrator decided to act" and "a process is
running on a node": **supervision** (start / stop / status of long-lived daemons)
and **deployment** (getting code onto the node in the first place). Both are
implemented as **pure command synthesis** — they build the exact shell string or
argv to run, which makes them fully unit-testable — with a thin runner doing the
actual I/O.

## Supervision (`core/supervisor.py`)

Node hosts are mixed: the roleA host may have systemd, the roleB host may not. So
`ccFleet` uses a uniform **detached + pidfile** scheme, and *prefers* systemd only
where a unit is installed (principle **P5**).

**Start (detached):**

```
mkdir -p /tmp/ccflet && cd <dir> && setsid nohup <command> >/tmp/ccflet/<name>.log 2>&1 </dev/null &
echo $! > /tmp/ccflet/<name>.pid
```

`setsid` + `nohup` + redirecting all three streams detaches the process from the
SSH session so it survives disconnect; the pidfile is the handle for stop/status.

**Status resolution order** (`status_cmd` + `parse_status_output`):

```
pidfile (/tmp/ccflet/<name>.pid alive?) → pgrep <match> → systemctl is-active (if prefer_systemd)
```

**Stop:** systemd unit if preferred and installed, else kill the pidfile PID, else
`pkill -f <match>`. `unit_installed_cmd` checks whether a unit exists before
trusting systemd.

All of this is built by pure functions (`detached_start_cmd`, `systemd_start_cmd`,
`status_cmd`, `stop_cmd`, `parse_status_output`); the `Supervisor` class binds them
to an SSH client. `PID_DIR = /tmp/ccflet` is the one shared location.

## Deployment / transfer (`core/transfer.py`)

Bulk code transfer does **not** go over paramiko — it shells out to the system
tools, which are faster and battle-tested for large trees (principle **P4**,
hybrid):

| Target | Tool | Why |
|---|---|---|
| roleA push | `rsync -az -e "ssh <opts>"` | incremental, fast re-deploys |
| roleB push | `scp -O -J <jump>` | a host without sftp; `-O` forces legacy scp; `-J` jumps via roleA |

`rsync_push_cmd` and `scp_to_roleB_cmd` synthesize the argv; `run_transfer` runs it
as a subprocess and captures result, honoring `dry_run` (print, don't execute).
The same `ssh_opts` / `key_file` from `fleet.yaml` are threaded into both tools so
there is one auth source for paramiko *and* the system tools.

## Key decisions

- **Pure synthesis, thin runner.** The command strings are computed by tested
  functions; only the final exec is I/O. A wrong flag is caught in a unit test.
- **Detached + pidfile as the floor, systemd as an upgrade** — works on a host
  without systemd and one with it alike, with no special-casing in the orchestrator.
- **System rsync/scp for bulk, paramiko for control** — each tool where it's best;
  one shared SSH auth config.
- **`scp -O`** (legacy protocol) for a roleB host whose SSH lacks the modern sftp
  subsystem.

## Constraints / Invariants

- A daemon's `name` ↔ its pidfile (`/tmp/ccflet/<name>.pid`); the supervisor, the
  profiles ([04](04-action-profiles.md)) and the mock ([11](11-mock-and-testing.md))
  all key off the same name.
- `ccFleet` cleans up only the streams/processes it started; node `*.run` scripts
  spawning their own tails must not be killed.
- Any value interpolated into a synthesized command is single-quoted / validated —
  see [12 — Security](12-security-and-operations.md).

## Change points

- **Change the start recipe** (extra env, working dir, log path) →
  `detached_start_cmd` / `systemd_start_cmd` in `supervisor.py`.
- **Change status precedence** (e.g. trust systemd first) → `status_cmd` +
  `parse_status_output`.
- **Change deploy transport** (e.g. tar-pipe, different flags) →
  `rsync_push_cmd` / `scp_to_roleB_cmd`, then `tests/test_transfer.py`.
- **Move the pidfile dir** → `PID_DIR` (also update the mock's matchers).

## Open questions

- No log rotation on `/tmp/ccflet/<name>.log`; long runs grow unbounded on the node.
- No transfer retry / resume beyond what rsync gives for free; a flaky link mid-scp
  fails the action and relies on re-deploy.
