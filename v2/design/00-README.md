---
noteId: "f956d4c06b0211f1b060577f73b9a94a"
tags: []
title: "Overview & Index"
order: 0
summary: "What the ccFleet fleet command & control template is, and a map of these design docs."

---

# ccFleet — Fleet Command & Control · Design Tree

**ccFleet** is a generic, domain-free web template for bringing a fleet of up to
~10 remote nodes **up** for a run, **watching** their health, and bringing them
**down** again — entirely over SSH, with **every action and result recorded**. It
ships with two abstract per-node roles (`roleA`/`roleB`), three services
(`serviceA`/`serviceB`/`serviceC`), and a config-driven **variant** mechanism, all
of which you re-map to your own domain by editing config — no code change.

It is strictly an **ops / control plane**: deploy code, manage daemon lifecycles,
stream logs, and compute health gates. The control path `ccFleet → node` is always
SSH; the app never ingests application telemetry of its own — that belongs to
whatever the nodes actually run.

This `design/` tree is the engineering map of the system, decomposed by concern.
Each file stands alone and follows the same shape:

- **Overview** — what the concern covers and why it matters.
- **Key decisions** — the non-obvious choices, and why we made them.
- **Constraints / Invariants** — what the implementation must never break.
- **Change points** — the knobs you can ask us to tune, and the file they live in.
- **Open questions** — trade-offs not yet settled.

## How to read this

Start with **[01 Architecture](01-architecture.md)** for the shape of the system,
then **[02 Scope & Principles](02-scope-and-decisions.md)** for the rules of the
game. After that, dip into whichever concern you want to understand or change. If
you want something different, find the matching doc, read its **Change points**,
and tell us which one to turn.

## Index

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [Architecture](01-architecture.md) | System shape, data flow, module map, pure vs I/O split |
| 02 | [Scope & Design Principles](02-scope-and-decisions.md) | Mission, what's in / out of scope, the design principles |
| 03 | [Fleet Model & Variants](03-fleet-and-variants.md) | Nodes, the inventory, variants A/B, derived params |
| 04 | [Action Profiles](04-action-profiles.md) | Per-role action catalogs, `{param}` substitution, jump-host |
| 05 | [Orchestration & Sequencing](05-orchestration-and-sequencing.md) | The engine: fan-out, variant-aware bring-up / tear-down, invariants |
| 06 | [Supervision & Deployment](06-supervision-and-deployment.md) | Daemon lifecycle (pidfile / systemd), code transfer |
| 07 | [Health Monitoring & Gates](07-health-and-gates.md) | Collectors, parsers, GATE A–D, thresholds |
| 08 | [Connectivity & Live Streaming](08-connectivity-and-streaming.md) | SSH + jump-host, `exec_stream`, live log tailing |
| 09 | [Sessions & Audit Trail](09-sessions-and-audit.md) | Ops sessions, the append-only event log, ZIP export |
| 10 | [Web UI & Realtime](10-web-ui-and-realtime.md) | REST pages, SocketIO rooms, multi-operator presence |
| 11 | [Mock Fleet & Testing](11-mock-and-testing.md) | The simulated fleet, the test strategy, coverage |
| 12 | [Security & Operations](12-security-and-operations.md) | No-auth posture, shell / path / XSS safety, run & deploy |
| 13 | [Config & Commands](13-config-and-commands.md) | Config over code: edit logic from the browser, operator-defined remote & local commands |
| 14 | [Connectivity LEDs](14-connectivity-leds.md) | The top-bar link-reachability ping LEDs, driven by operator-editable `networks.yaml` |

> **In the repo but not a design *concern*:** `scripts/` holds standalone
> base-station **CLI helpers** (e.g. `scripts/clear_sessions.sh`, which wipes every
> ops session under `runs/`). They are **not** the operator command catalog
> (`commands/`, doc 13) — nothing in `ccFleet` runs them, they're not on the UI, and
> they're not validated / hot-reloaded / audited. See `scripts/README.md` and
> [09 Sessions & Audit](09-sessions-and-audit.md).

## The system in one picture

```
   Operators' browsers
          │  HTTP + WebSocket
          ▼
   Flask + SocketIO  ── web/routes.py · core/sync.py
          │
   CCFletApp facade  ── app.py   (owns session, per-node variant & fleet algo)
          │
   Orchestrator  ── core/orchestrator.py   (the engine)
          │
   ┌──────┴───────────────┬───────────────────────┐
   ConnectionPool      sequences / fan-out      status poller
   (real | mock)       (variant-aware)          (collectors → GATEs)
          │                  │                       │
   ssh_client / mock_ssh   supervisor + transfer   status.py
   (paramiko + jump)       (command synthesis)     events · storage · sync
```

## Source of truth

The **code is the truth**; these docs explain *intent*. When a doc disagrees with
the code, fix one of them — don't guess. Deeper references live beside the app:
`plan1.md` (the implementation plan), `README.md` (quick start), and `CLAUDE.md`
(the engineering brief for changing this code).
