---
noteId: "736dddd06b0311f1b060577f73b9a94a"
tags: []
title: "Connectivity & Live Streaming"
order: 8
summary: "The SSH layer (paramiko + jump-host), the streaming-vs-execute distinction, and live log tailing to xterm."

---

# Connectivity & Live Streaming

## Overview

Every control action and every log line reaches the operator over SSH. `ccFleet`
uses **paramiko** for interactive control (exec, status, streaming, jump-host), and
the system `rsync`/`scp` only for bulk transfer
([06](06-supervision-and-deployment.md)). This doc covers the SSH client, the
jump-host hop to roleB, the critical separation between **lock-serialized
`execute()`** and **lock-free `exec_stream()`**, and how live tails flow to the
browser's xterm panes.

## The SSH client (`core/ssh_client.py`)

A thin wrapper over a paramiko `SSHClient`, configured from a profile connection
(`ConnectionConfig.from_profile_connection`). Two execution paths, deliberately
different:

- **`execute(cmd)`** — run one command to completion, capture stdout/stderr/rc,
  return a `CommandResult`. **Lock-serialized for the whole command** so concurrent
  callers don't interleave on one transport.
- **`exec_stream(cmd, on_line)`** — open a **dedicated channel** and stream output
  line-by-line for live tailing. It **snapshots the client under the lock, then
  reads lock-free**, so a long-running tail never holds the execute lock (a race
  fix — a tail must not block control commands).

> **Rule:** never stream through `execute()`. A `tail -F` there would hold the lock
> forever and freeze all control traffic to that node.

## The jump-host (roleB via roleA)

The roleB host (`<subnet>.2`) is not directly reachable; it sits behind the roleA
host. When a connection profile sets `via` ([04](04-action-profiles.md)),
`_open_jump_channel` opens a paramiko `direct-tcpip` channel from the roleA
connection to roleB and tunnels the roleB SSH session through it — one hop, no agent
on roleB. Variant B only.

## Connection pooling

The orchestrator's `ConnectionPool` keeps one client per `(node, role)` and reuses
it across actions, building it lazily via the factory (real or mock). `close_all()`
tears them down on shutdown. This keeps action latency low (no per-action SSH
handshake) and bounds open sockets.

## Live streaming (`core/streaming.py` → `core/sync.py` → xterm)

```
operator opens a log pane
  → subscribe_log(node, logname)            [sync.py SocketIO handler]
  → StreamManager starts a _Tail thread      tail -F <path> via exec_stream
  → each line → emit to the stream room      [sync.py: stream_room(node, log)]
  → xterm.js renders it in the browser        [web/static/js + vendored xterm]
```

`StreamManager` caps concurrent tails at `MAX_TAILS = 24`, dedupes subscribers per
`(node, log)`, and `stop`/`stop_all` tears down **only the tails `ccFleet` started** —
node `*.run` scripts that spawn their own `tail -f` must be left alone. Log paths
come from the profile `logs:` map.

## Key decisions

- **paramiko for control, system tools for bulk** (hybrid, principle **P4**) — exec
  and streaming need fine-grained channel control; bulk copy is better served by
  rsync/scp.
- **`async_mode='threading'` + simple-websocket, not gevent** — gevent's
  monkey-patching is hazardous next to paramiko's own threads; threading keeps the
  two cleanly separate.
- **Execute is locked, streaming is not** — the single most important concurrency
  decision in the I/O layer; it's what lets a live tail and a control command share
  a node without deadlock.
- **One jump channel, no roleB agent** — roleB needs nothing installed; `ccFleet`
  tunnels through the roleA host it already has.

## Constraints / Invariants

- Never stream through `execute()`; use `exec_stream` on its own channel.
- `StreamManager` must only stop streams it owns.
- One auth source: `key_file` / `ssh_opts` in `fleet.yaml` feed paramiko *and* the
  system tools.
- Respect `MAX_TAILS`; a runaway subscriber count must not exhaust node channels.

## Change points

- **Tune concurrent tails** → `MAX_TAILS` in `streaming.py`.
- **Add a tailable log** → the profile `logs:` map ([04](04-action-profiles.md)).
- **Change SSH options / timeouts** → `ssh_opts` in `fleet.yaml` (one source).
- **Add a second jump hop / bastion** → `_open_jump_channel` +
  `ConnectionConfig` parsing.

## Open questions

- Streaming has no backpressure: a very chatty log floods the socket; a
  rate-limit/coalesce step is a candidate.
- No automatic SSH reconnect/backoff in the pool — a dropped connection fails the
  next action and is rebuilt lazily; an explicit retry policy is unscoped.
