---
noteId: "651ed7706b0311f1b060577f73b9a94a"
tags: []
title: "Health Monitoring & Gates"
order: 7
summary: "How operator-editable gate config becomes per-node readiness cells (config over code, P8)."

---

# Health Monitoring & Gates

## Overview

Health is the operator's primary readout: is the fleet ready? Each node carries a row of
**gates** — colored readiness cells — that roll up into the node's card color. Gates are
**operator-editable config, not code** (P8): each `gates/*.yaml` is one gate that declares
*what to run, where, and how the result maps to a color*. The operator retunes a host, a
process list, a command or a threshold from the **Config** page (the **Gates** root);
saves are validated → backed up → hot-reloaded → audited with no restart — exactly like
the States bar and the custom-command catalog.

The generic engine `core/gates_config.py` parses + validates the YAML and evaluates the
pure parts (field extraction, condition/level evaluation, color→severity); the
orchestrator (`core/orchestrator.py`) runs the transport per node and folds the results
into a `NodeStatus`. The UI builds the gate cells client-side from `GET /api/gates`, so a
gate edit changes the cells with no template change.

## The three gate kinds

| Kind | Runs | Colors by |
|---|---|---|
| **reach** | a role's reachability — an SSH control-plane connect (the truth, also the short-circuit) or an ICMP `ping` of a host | `colors.{up,down}` |
| **process** | a list of processes that must be running (a `check` command per entry, exit 0 ⇒ up); each entry mandatory or optional, optionally variant-scoped | all up → `all_up`; an optional one down → `optional_down`; a mandatory one down → `mandatory_down` |
| **metric** | a command whose output yields **fields** (regex groups or JSON keys, typed int/float/bool) | the first matching **level** (`when` conditions) → its color |

A gate declares common keys (`key`, `label`, `kind`, `on` ∈ `base|roleA|roleB`,
`variants`, `timeout`, `interval`, `hint`) plus its kind's fields. See `gates/README.md`
for the full schema-by-example and the four generic starter gates (A reach · B proc ·
C check · D link).

## Colors → severity → card rollup

Configs speak **named colors** (the same palette as the States LEDs:
`green/yellow/red/blue/purple/orange/gray`). The engine derives a **severity** for the
rollup so the operator only ever picks colors:

```
green → ok      yellow|orange → warn      red → fail      gray → na      blue|purple → ok
```

Each gate result carries **both** `color` (what the cell shows) and `state` (the
severity). `core/status.py::overall_gate` rolls the per-gate severities into one card
color (worst wins; `na` ignored), unchanged from before — so a config-only change of
colors never breaks the card coloring or the Compiler acceptance gate.

## Evaluation (the orchestrator poll)

`Orchestrator.poll_node` is registry-driven:

1. **Per-tick reachability, once per role.** Each needed role is connected once and the
   result cached for the tick. An `ssh` reach gate is the control-plane truth and
   **short-circuits**: if a role won't connect, that role's gates resolve to `fail`
   immediately instead of stacking SSH timeouts.
2. **Each due gate** (its `interval` elapsed, or a forced/on-demand poll) is evaluated in
   parallel via its kind's runner; not-due gates keep their cached result. A gate not
   applicable to the node's current variant is `na`.
3. **Publish.** A `NodeStatus{node, variant, reachable_*, gates}` is stored and broadcast.
   `GATE_CHANGED` fires only when a gate's **color** changes (so metric value-jitter at the
   same color is quiet), and each individual color flip drops a human-readable session-log
   line (P6, like the States bar's `STATE_CHANGED`).

The poll loop ticks at the most-frequent gate's `interval` (floored at 1 s).

## Mock & dry-run

Under `--mock`/`--dry-run` nothing touches the wire. `--mock` produces each gate from the
**simulated world** via `domain/mock_rules.gate_mock` (reach → reachable?, process → the
simulated daemons so a bring-up flips the proc gate green, metric → the gate's `mock`
block once its `up_when` daemon is up). `--dry-run` with the real factory returns a healthy
preview. The local `base`/`ping` path is echo-only under mock/dry-run and disabled by
`--no-local-commands`.

## Purity split (what's tested where)

- **Pure** (`core/gates_config.py`, `tests/test_gates_config.py`): schema parse/validate
  per kind, field extraction (regex + json), condition + level evaluation, color→severity,
  variant gating, the registry (load / reload-in-place / cross-file key clash).
- **Orchestrator** (`tests/test_orchestrator.py`): the registry-driven evaluation against
  the mock + a fake client — reach short-circuit, per-gate interval, the real
  process/metric/reach transport.
- **HTTP** (`tests/test_routes.py`): `/api/gates`, gate-driven node status, gate hot-reload.

## Key decisions

- **Config over code (P8).** The logic that decides "ready / not ready" is YAML an
  operator edits in the browser, not a code change + recompile. The engine is generic and
  template-level; the four gate YAMLs are the per-app slice.
- **The `mock ↔ status` string contract dissolves for gates.** The mock no longer parses
  gate text; `gate_mock` keys off the simulated world. (The live-log producers still use
  the demo log vocabulary in `domain/gates.py`.)
- **Severity == the old gate vocabulary**, so the card rollup, the tab dots and the
  Compiler acceptance gate are unchanged.
- **`warn` is first-class** (yellow/orange) — uncertain conditions don't read as hard
  failures.
- **Variant-gating** (`variants:` on a gate or a process entry) so a variant isn't
  penalized for a check it doesn't run.

## Constraints / Invariants

- Keys and reach hosts are validated as bare tokens; an unknown color is a line-numbered
  error on save, not a dark cell.
- A node-derived `{param}` substituted into a gate command is a bare token (shell safety);
  a gate takes no free-form runtime args.
- `on:` is a YAML 1.1 boolean-key gotcha (a bare `on:` key parses as `True`); the parser
  reads it anyway, so the operator can write `on: roleA` naturally.

## Change points

- **Tune / add / remove a gate** → edit `gates/*.yaml` from the Config page (no code).
- **Add a new gate kind** (beyond reach/process/metric) → `core/gates_config.py`
  (engine) + a runner in `core/orchestrator.py` + the mock branch in
  `domain/mock_rules.gate_mock`.
- **Change the overall roll-up** → `overall_gate` in `core/status.py`.

## Open questions

- Gate state is computed each poll with no hysteresis; a flapping signal flaps the cell.
  Debounce/hold-down is a candidate if field noise proves annoying.
- Mesh peer-age freshness and the v2v probes that the *old* hard-coded gates encoded are
  not expressed by the generic starter gates; a fork re-adds them as a `metric` gate or a
  States LED.
