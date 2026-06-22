# ccFleet — architecture & design principles

This is the source-of-truth overview for **ccFleet**, a generic SSH command-&-control
template for a fleet of remote nodes. It states the **mission**, the **design
principles** (P1–P8) that constrain the code, and the **architecture** at a glance.
`CLAUDE.md` is the working engineering brief; `design/*.md` is the per-subsystem
reference (served on the Help page). The source code is the truth — if a fact here
disagrees with the code, fix one of them.

## 0. Mission & principles

**Mission:** bring a fleet of remote nodes **up**, **watch** its health, and bring it
**down** — over SSH — with **everything recorded**. ccFleet is strictly an ops/control
plane (deploy · daemon lifecycle · logs · health). It is a clean, domain-free template:
two roles (roleA, roleB), three demo services (serviceA/B/C), a per-node variant (A/B),
four health gates (A–D) — all placeholders to fill in.

The locked design principles:

- **P1 — Per-node variant.** Each node carries its own variant (A/B) at runtime; a
  variant selects a config-driven parameter set. No global selector — each node
  carries its own variant.
- **P2 — SSH-only control plane.** The path `ccFleet → node` is always SSH. Ops plane
  only — no telemetry ingest, no out-of-band channels.
- **P3 — Port the proven parts.** Reuse the audit/storage/sync/ssh scaffolding and the
  Flask + SocketIO + xterm stack rather than re-inventing them.
- **P4 — Hybrid SSH/transfer.** paramiko for exec/status/stream/jump-host; system
  `rsync` (roleA push) and `scp -O` (roleB, through the jump-host) for bulk transfer.
- **P5 — Detached + pidfile supervision.** Start daemons with `setsid nohup … & echo $!
  >pid`; status via pidfile → pgrep → systemd; prefer systemd where a unit exists.
- **P6 — Audit-only safety.** No confirm prompts, no dry-run gating in the UI. Every
  action *and* its result is appended to `events.jsonl`. The audit log is the safety net.
- **P7 — No provisioning.** Assume nodes are already reachable; do the per-run loop only.
- **P8 — Config over code.** The operator who runs ccFleet can change the fleet,
  profiles, variants, command catalog and connectivity checks from the **Config** page —
  validated → hot-reloaded → audited — without touching source. New operator-facing
  logic lands as config, not hard-coded behavior. (Top objective.)

## 1. Architecture

```
browser ──HTTP/WebSocket──► Flask + SocketIO (web/routes.py, core/sync.py)
                                   │
                              CCFletApp facade (app.py)
                                   │
                              Orchestrator (core/orchestrator.py)
             ┌─────────────────────┼───────────────────────────────┐
        ConnectionPool        variant-aware sequences           status poller
        (real | mock)         + fan-out                         (collectors→GATE A–D)
             │                       │                                │
   ssh_client / mock_ssh      supervisor + transfer            status.py (pure)
```

The logic that can be wrong (param derivation, sequencing, command synthesis, parsing,
gating) is **pure** and unit-tested with no network; the I/O shells are thin and
exercised by the `--mock` boot. See `CLAUDE.md §4` for the module map.

## 2. The node model

- **Roles.** `roleA` is the primary host, reached directly over SSH. `roleB` is a
  secondary host reached *through* roleA as a jump-host (`connection.via`), used only in
  variant B. Each role has a profile (`profiles/{roleA,roleB}.yaml`).
- **Services.** The example fleet runs `serviceA` + `serviceB` on roleA and `serviceC`
  on roleB (variant B only) — placeholders for whatever your nodes run.
- **Variant.** Per-node A/B. A variant selects a config-driven parameter set
  (`defaults.variants` in `fleet.yaml`: `addr`/`launcher`/`flag` → `VAR_ADDR`/
  `VAR_LAUNCHER`/`VAR_FLAG`). `core/fleet.py::Fleet.params(node)` derives the full
  substitution dict; derived values are never hand-typed.

## 3. Sequencing (the heart)

`core/orchestrator.py` runs variant-aware, ordered sequences and enforces the ordering
invariants in code:

```
DEPLOY   (per node) : rsync serviceB + serviceA  [+ build serviceA]
BRING-UP (variant A): serviceA_start ─(healthy?)─► serviceB_start
BRING-UP (variant B): serviceC_start@roleB ─►(healthy?)─► serviceA_start ─►(healthy?)─► serviceB_start
TEAR-DOWN(A)        : serviceB_stop ─► serviceA_stop
TEAR-DOWN(B)        : serviceB_stop ─► serviceA_stop ─► serviceC_stop@roleB
```

Invariants: **serviceA before serviceB**; **serviceC before serviceA** (variant B);
identical `$ID` across services. Fleet variants fan the per-node sequence across the
selection with a configurable `stagger`.

## 4. Health gates (A–D)

`core/status.py` folds collected signals into a `NodeStatus` and maps it to four gates,
gated by the node's variant:

- **A — reach:** roleA reachable (+ roleB reachable + probe A/B in variant B).
- **B — proc:** serviceA + serviceB up (+ serviceC in variant B).
- **C — check:** a variant-B sensor/value check (N/A in variant A).
- **D — link:** peer/link liveness (+ serviceC transport stats in variant B).

Thresholds are constants at the top of `status.py`.

## 5. Config over code (P8)

The **Config** page edits four roots — `fleet/`, `profiles/`, `commands/`, `networks/`
— through a path-safe store (`core/config_store.py`). Every save is **validated** into
the real model, the prior file is **backed up** to `<root>/.bak/`, the change is
**hot-reloaded** in place with no restart, and a `CONFIG_SAVED` + `CONFIG_RELOADED`
event is audited. The operator command catalog (`commands/commands_{host,roleA,roleB}.
yaml` + `*.sh`) renders to buttons client-side, so editing a file + reload changes the
UI with no template edit. Remote (🛰) vs local (🖥) commands are visually distinct.

## 6. Sessions & audit

A session is one ops run dir under `runs/` (`manifest.json` + `events.jsonl` +
`fleet_snapshot.yaml` + `logs/` + `artifacts/`), ZIP-exportable. Every action and its
result is appended to the session's append-only `events.jsonl` (P6).

## 7. Mock & testing

`--mock` swaps the SSH factory for a stateful in-memory fleet (`core/mock_ssh.py`) that
pattern-matches the real synthesized commands, so the whole stack is exercised end-to-end
with no hardware. `--dry-run` prints the commands without running them. The test suite is
pure-logic units + a mock-backed integration suite. The `mock_ssh.py ↔ status.py` string
contract is the sharpest edge — verify the live `--mock` boot, not just `pytest`.

## 8. Out of scope (by design)

Auth/RBAC (closed LAN, trusted operators, bind to a chosen interface, audit everything);
one-time provisioning (ccFleet assumes nodes are reachable and does the per-run loop only).
