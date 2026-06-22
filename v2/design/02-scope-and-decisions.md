---
noteId: "139ce7c06b0311f1b060577f73b9a94a"
tags: []
title: "Scope & Design Principles"
order: 2
summary: "The mission, what is deliberately in and out of scope, and the core design principles."

---

# Scope & Design Principles

## Overview

This doc is the "rules of the game." It states what `ccFleet` is for, what it
deliberately does **not** do, and the design principles that shape every other
concern in this tree. The principles are load-bearing; reversing one is a re-opening
of the design, not a tweak.

## Mission

> Bring a fleet of up to ~10 remote nodes **up** for a run, **watch** it,
> and bring it **down** — over SSH, with **everything recorded.**

`ccFleet` is the **ops / control plane**: deploy · daemon lifecycle · logs · health.
The control path `ccFleet → node` is **always SSH**.

## In scope

- Deploy code to nodes, build where needed.
- Start / stop / status the node daemons in the correct, variant-aware order.
- Stream live logs to the operator.
- Poll health and compute GATE A–D per node and fleet-wide.
- Record every action and result; export a session as a ZIP.
- Multiple operators watching the same fleet at once.
- **Edit the logic from the browser** — fleet inventory, role profiles, variants,
  and a catalog of extra triggerable commands (remote and local) are
  operator-editable config served by the **Config** page, with no code change or
  restart.

## Out of scope (do not add without re-opening the design)

- **Application-layer behavior of the nodes themselves** — whatever the deployed
  services do at runtime is their concern, not the control plane's. `ccFleet` never
  ingests their application data; it only deploys, supervises, and reads health.
- **One-time provisioning** — image build, OS setup, cross-compile, first-boot
  registration. `ccFleet` assumes every node is already provisioned and booted; it
  runs the **daily / per-run loop only**.
- **Auth / RBAC** — closed LAN, trusted operators, bind to a chosen interface, audit
  everything. See [12 — Security](12-security-and-operations.md).

## The design principles

| # | Principle | What it means for the code |
|---|-----------|----------------------------|
| **P1** | Both variants in scope; variant is **per-node** | `Fleet.node_variants` is per-node runtime state — each node has its own A/B toggle on its card/detail (no global selector). A fleet can run mixed variants by group; keeping any *coordinating* group on one variant is the operator's job, **not enforced** (P6). |
| **P2** | `ccFleet → node` is **always SSH**; ops plane only | No application client, no domain commands, no telemetry ingest of the node's own data. |
| **P3** | Port proven I/O scaffolding | Reused `events / storage / sync / ssh_client` + the Flask/SocketIO scaffold + xterm assets. The control engine itself was written fresh as `orchestrator.py`. |
| **P4** | **Hybrid** SSH / transfer | paramiko for exec / status / stream / jump; system **`rsync`** (roleA push) + **`scp -O`** (roleB push) for bulk transfer. See [06](06-supervision-and-deployment.md). |
| **P5** | **Detached + pidfile** supervision, prefer systemd for serviceA | `setsid nohup … >log 2>&1 </dev/null & echo $! >pid`; status via pidfile → pgrep → systemctl. See [06](06-supervision-and-deployment.md). |
| **P6** | **Audit-only safety** — no confirm prompts, no dry-run gating in the UI | *Every* action **and** result → `events.jsonl`. The audit log is the safety net. (`--dry-run` exists for dev / test only.) See [09](09-sessions-and-audit.md). |
| **P7** | **No provisioning** | Assume provisioned + booted; run the per-run loop only. |
| **P8** | **Config over code** — the operator changes logic without touching code | Fleet inventory, role profiles, variants, and the catalog of triggerable commands live in **operator-editable YAML + scripts served by the Config page**: validated → hot-reloaded → audited. New operator-facing logic is config, not hard-code. Adds `commands/` (remote + local commands), `networks/`, and `core/{config_store,commands,networks,local_exec}.py`. Full doc: [13 — Config & Commands](13-config-and-commands.md). |

## Constraints / Invariants

- These principles are honored throughout the code; weakening one is a design
  change, not a refactor. Each downstream doc references the principles it depends
  on.
- "Audit everything" (P6) is the *only* safety mechanism — there are no
  confirmation dialogs by design. Anything that mutates a node must be logged.
- **Config-as-code (P8) is intentional, not a hole to plug.** A trusted operator
  authoring YAML/scripts/commands that run arbitrary shell *is the feature*; it is
  fenced by the same posture as the rest of the app (closed LAN, no auth, bound to a
  chosen interface, everything audited), and by validation-before-apply. Local
  (base-station) commands are the higher-blast-radius path and are echo-only under
  `--mock`/`--dry-run` and gated by `--no-local-commands`.

## Change points

- Want confirmation prompts or RBAC? Those reverse P6 / the security posture — we
  can do them, but flag it as a scope change so we re-open the relevant principle
  deliberately.
- Want `ccFleet` to read or act on the nodes' own application data? That reverses P2
  and is a major scope expansion.
- Want a new editable config file or a new kind of triggerable command? That is
  exactly what P8 is for — add it to `config_store`/`commands` and it shows up in the
  Config page; no principle re-opening needed.

## Open questions

- **Per-group variants** (some nodes A, some B) ships today; the param-derivation
  model in [03](03-fleet-and-variants.md) is per-node.
- Whether a future read-only "viewer" role is worth introducing without full RBAC.
