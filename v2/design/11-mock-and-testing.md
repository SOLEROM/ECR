---
noteId: "9da8b7f06b0311f1b060577f73b9a94a"
tags: []
title: "Mock Fleet & Testing"
order: 11
summary: "The stateful simulated fleet that runs the whole stack without hardware, and the test strategy."

---

# Mock Fleet & Testing

## Overview

`ccFleet` can run its entire stack — bring-up, health, streaming, gates going green —
**without any node hardware**, via a stateful simulated fleet. This is both the
demo/dev mode (`--mock`) and the backbone of the integration tests. Paired with
exhaustive **pure-logic unit tests**, it means almost the whole system is verifiable
offline, on a laptop, with no real hosts.

## The mock fleet (`core/mock_ssh.py`)

`--mock` swaps the connection factory so every `(node, role)` gets a
`MockSSHClient` bound to a shared `MockFleetState`. The mock **pattern-matches the
real synthesized commands** against an in-memory world:

- It recognizes the actual supervisor start/stop/status strings, the collectors,
  the probe queries, and `tail` invocations — keyed off stable substrings
  (`/tmp/ccflet/<name>.pid`, `setsid nohup`, `links.json`, `serviceA.rx`,
  `serviceB.log`, `serviceC.log`, `probeA`, `probeB`).
- `MockFleetState` tracks per-node daemon state, links/peers, value-check lines,
  serviceC stats, probe A/B, and simulated transfers. `set_offline()` makes a node
  unreachable (red GATE A).

Because it matches *real* commands, the supervisor, orchestrator and parsers are
genuinely exercised — **only the wire is faked**. A mock bring-up flips daemons up
→ links come alive → collectors emit fresh lines → gates go green, exactly like
the real thing.

> **Rule:** when you add or change a remote command, teach the mock to recognize it.
> Otherwise its unit tests can pass while `--mock` shows nothing happening.

## The test pyramid

| Layer | Driven by | Covers |
|---|---|---|
| Pure unit | direct calls + golden fixtures | `fleet`, `profiles`, `supervisor`, `status`, `transfer`, `storage`, `commands`, `networks` |
| Engine integration | the **mock backend** | `orchestrator` sequences, polling, fan-out |
| Supervisor I/O | `FakeSSH` (records cmds, canned results) | start/stop/status command flow |
| Full stack | `--mock` boot (manual / smoke) | `ssh_client`, `sync`, `streaming` shells |

`tests/conftest.py` provides the shared `FLEET_DICT` fixture and `FakeSSH`. The
suite is **~181 tests, no network** (incl. the config store, command catalog,
networks/LED monitor, local exec, and Flask test-client route checks).

## Coverage posture

Pure logic carries the coverage and is held to **≥80%**: `fleet` ~93%, `profiles`
~90%, `status` ~92%, `supervisor` ~96%, `storage` ~95%, `orchestrator` ~82%,
`transfer` ~83%. The I/O shells (`ssh_client`, `sync`, `streaming`) are covered by
the `--mock` boot rather than unit tests — a deliberate trade-off. Make one of them
non-trivial and it earns a targeted test.

## `--dry-run`

`--dry-run` synthesizes and **prints** the real SSH/transfer commands without
executing them — the cheap way to eyeball exactly what would hit the fleet, no
hardware and no mock.

## Key decisions

- **Match real commands, fake only the wire.** The mock is high-fidelity because it
  drives the same synthesis path as production — so it catches sequencing/parsing
  bugs, not just transport bugs.
- **Pure logic gets the unit tests; shells get the mock boot.** Test where bugs
  actually hide (logic), don't chase coverage on thin adapters.
- **Golden fixtures for parsers/gates** — the behavior most likely to regress
  subtly is pinned to representative real output.

## Constraints / Invariants

- Keep the mock in step with real commands; a new command needs a new matcher.
- Pure modules stay ≥80% covered; new pure logic ships with a test.
- Tests run with **no network** — anything needing a socket belongs behind the mock.

## Change points

- **Add a simulated signal / failure** → `MockFleetState` in `mock_ssh.py`.
- **Add a command matcher** → `MockSSHClient` in `mock_ssh.py`.
- **Add a fixture / shared object** → `tests/conftest.py`.
- **Add a golden parser/gate case** → `tests/test_status_parsers.py`.

## Open questions

- The mock simulates steady-state success well; richer fault injection (flapping
  links, partial checks, mid-sequence death) is only partially modeled.
- There is no live-hardware CI; the real node end-to-end path is the main
  untested surface — exercise it on one node first (see [12](12-security-and-operations.md)).
