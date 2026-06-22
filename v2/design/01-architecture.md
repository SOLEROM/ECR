---
noteId: "07cbb2a06b0311f1b060577f73b9a94a"
tags: []
title: "Architecture"
order: 1
summary: "The shape of the system — data flow, module map, and the deliberate pure-vs-I/O split."

---

# Architecture

## Overview

`ccFleet` is a Flask web app with a thin REST + WebSocket surface in front of a single
**orchestration engine**. The browser shows fleet state and triggers actions — and
**those actions, and the logic behind them, are themselves operator-editable
config** (fleet inventory, role profiles, variants, and a catalog of extra
triggerable commands), changed from the in-app **Config** page with no code edit and
no restart (**config over code** — see [02](02-scope-and-decisions.md)). The engine
fans the actions out to nodes over SSH (or runs **local** ones on the base station),
supervises daemons, polls health, and streams logs back. The whole thing is built so
that **everything that can be logically wrong is pure and unit-tested**, while the
parts that touch the network are thin shells exercised by a simulated fleet.

## Data flow

```
browser ──HTTP/WebSocket──► Flask + SocketIO (web/routes.py, core/sync.py)
                                   │
                              CCFletApp facade (app.py)  ── owns session, variant/algo
                                   │
                              Orchestrator (core/orchestrator.py)  ◄── the engine
             ┌─────────────────────┼───────────────────────────────┐
        ConnectionPool        sequences / fan-out               status poller
        (real | mock)         (variant-aware)                    (collectors→GATE)
             │                       │                                │
   ssh_client / mock_ssh      supervisor + transfer            status.py (pure)
   (paramiko + jump-host)     (cmd synthesis)                  events/storage/sync
```

A typical action: browser POSTs → `routes.py` → `CCFletApp.run_bg` dispatches a
background task → `Orchestrator` renders the action from the node's params +
role profile → synthesizes a shell command → runs it through a pooled SSH client
→ records request + result to the session audit log → broadcasts the outcome over
SocketIO to every connected operator.

## Module map (`core/`)

| File | Role | Purity |
|---|---|---|
| `fleet.py` | inventory model + per-node **param derivation** (variant A/B) | **pure** |
| `profiles.py` | action / collector / log schema + `{param}` render + `via` jump | **pure** |
| `supervisor.py` | daemon start / stop / status **command synthesis** + parse | **pure** |
| `status.py` | collector **parsers** → `NodeStatus` → **GATE A–D** | **pure** |
| `transfer.py` | rsync / scp -O command synthesis + subprocess runner | mostly pure |
| `commands.py` | **operator command catalog** (`commands/commands_{host,roleA,roleB}.yaml`) schema + `{param}` render | **pure** |
| `networks.py` | **base-station link** schema (`networks/networks.yaml`) — the top-bar LEDs | **pure** |
| `config_store.py` | **Config page** backend — path-safe read / validate / write / revert of the editable roots | mostly pure |
| `local_exec.py` | run a **local** (base-station) command as a subprocess → `CommandResult` | mostly pure |
| `result.py` | shared `CommandResult` (paramiko-free, so pure modules can import it) | **pure** |
| `orchestrator.py` | fan-out, **variant-aware sequences**, conn pool, poller, **`run_custom`** (the engine) | I/O |
| `net_monitor.py` | **ping poller** for the off-fleet links → `net_status` broadcast | I/O |
| `ssh_client.py` | paramiko wrapper + **jump-host** + `exec_stream()` | I/O |
| `streaming.py` | live `tail -F` → SocketIO rooms → xterm | I/O |
| `mock_ssh.py` | **stateful simulated fleet** (for `--mock` and tests) | sim |
| `events.py` · `storage.py` · `sync.py` | audit JSONL · session dirs / ZIP · multi-operator rooms | I/O |

`web/` holds `routes.py` (REST + page handlers), `templates/` (base, dashboard,
node, sessions, session_view, config, help) and `static/` (`main.css`, `app.js`,
vendored xterm + socket.io). `app.py` is the composition root: it builds the object
graph, exposes the `CCFletApp` facade, and parses the CLI.

## Key decisions

- **One engine, many thin shells.** All control logic lives in `orchestrator.py`;
  everything else is either pure logic it calls or an I/O adapter it drives. There
  is no business logic in the web layer or the templates.
- **Pure / I/O split is deliberate.** Param derivation, sequencing order, command
  synthesis, parsing and gating are all pure functions — no sockets, no clock
  dependence — so they can be tested exhaustively offline. See
  [11 — Testing](11-mock-and-testing.md).
- **A swappable connection factory.** The orchestrator never imports paramiko
  directly; it asks a `ConnectionPool` for a client. `--mock` swaps the factory
  for `MockSSHClient`, so the same engine runs against a fake fleet.
- **Threading, not gevent.** SocketIO runs `async_mode='threading'` with
  `simple-websocket` to avoid monkey-patch-vs-paramiko hazards. See
  [08 — Connectivity](08-connectivity-and-streaming.md).

## Constraints / Invariants

- New control logic should land on the **pure** side when possible, with a unit
  test, not inside an I/O shell.
- The web layer must stay a thin adapter — validate input, call the facade, return
  a response. No SSH, no sequencing, no parsing in `routes.py`.
- Keep files small and focused (200–400 lines typical, 800 hard max).

## Change points

- **Add a control capability** → extend `orchestrator.py` (and a profile action if
  it's a node command). [05](05-orchestration-and-sequencing.md)
- **Add an operator-triggerable command (no code)** → edit the right
  `commands/commands_{host,roleA,roleB}.yaml` from the Config page.
  [13](13-config-and-commands.md)
- **Add an editable config root / change validation / reload** → `config_store.py` +
  `CCFletApp.reload_config`. [13](13-config-and-commands.md)
- **Change the object graph / add a CLI flag** → `app.py` composition root.
- **Add a page or endpoint** → `web/routes.py` + a template. [10](10-web-ui-and-realtime.md)

## Open questions

- The engine is a single process with in-memory state; a second `ccFleet` instance
  would not share sessions. Multi-instance coordination is unscoped.
- No persistent job queue — background tasks live only in the running process; a
  crash mid-sequence is recovered by re-reading health, not by resuming the job.
