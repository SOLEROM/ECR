---
noteId: "651ed7706b0311f1b060577f73b9a94a"
tags: []
title: "Health Monitoring & Gates"
order: 7
summary: "How raw collector output becomes per-node status and the four readiness gates A–D."

---

# Health Monitoring & Gates

## Overview

Health is the operator's primary readout: is the fleet ready? `core/status.py`
turns raw collector output (link tables, value-check lines, serviceC stats, probe
state) into a structured `NodeStatus`, then reduces it to four **gates** — A, B, C,
D — each `ok` / `warn` / `fail` / `na`. The whole module is **pure**: parsers and
gate logic are functions of their text inputs, tested against golden fixtures, with
no network or clock.

## The parsers

| Parser | Reads | Produces |
|---|---|---|
| `parse_links` | `links.json` (preferred) or an rx-log tail (fallback) | peer/link ids + ages, count, `source` |
| `parse_check` | a tagged value line (`[CHECK]` / `[CHECK2]`) | present, `value`, `age` |
| `parse_servicec_stats` | the serviceC 1 Hz stats line | up/down rates, loop/self, errors, `signal` |
| `parse_probe_a` | the probe A status output | probe A READY? |
| `parse_probe_b` | the probe B status output | probe B OK? |

`parse_links` is dual-source by necessity: `links.json` is preferred when serviceA
writes it; otherwise it falls back to parsing a recent rx-log tail (coarser ages).
The result records which `source` was live, so the UI can say so.

## The four gates (`compute_gates`, variant-gated)

| Gate | Means | Variant A | Variant B |
|---|---|---|---|
| **A** | Reachability | roleA reachable | roleA + roleB reachable, probe A READY, probe B OK |
| **B** | Processes up | serviceA + serviceB | serviceA + serviceB + serviceC |
| **C** | Value check fresh | `na` (no check) | both checks present with the good value |
| **D** | Link liveness | enough fresh peers/links | same, plus serviceC transport stats; demoted to `warn` if stats missing |

`build_status` assembles a `NodeStatus` from raw inputs; `overall_gate` reduces a
node's four gates to one headline state (worst-of, with `na` ignored). A fleet view
rolls those up across nodes.

## Thresholds (constants at the top of `status.py`)

```
LINK_FRESH_MS   = 1000      # a link seen within 1 s counts as live
CHECK_GOOD      = 3         # the "good" check value
SERVICEC_MIN_UP = 15        # serviceC frames/s floor, slack allowed
SIGNAL_OK_RANGE = (-95, -40)  # serviceC signal sane window
```

## Gate logic nuances (encoded + tested)

- **Single-node fleet → GATE D is `na`**, not `warn` (no peers are *expected*).
- **Partial links** (some but not all expected peers) → `warn`, not `fail`.
- **Variant B with no serviceC stats** → link gate demoted to `warn` (can't confirm
  the transport).
- **GATE C only applies in variant B**; in variant A it is `na` by design (no check).

## Key decisions

- **Pure parsers + pure gates.** All the logic that decides "ready / not ready"
  is a function of text, so it's covered by golden-fixture unit tests
  (`tests/test_status_parsers.py`) — the part most likely to be subtly wrong.
- **Four orthogonal gates** rather than one opaque "healthy" flag — an operator
  sees *which* dimension failed (reachability vs processes vs check vs link).
- **`warn` is a first-class state** — partial/uncertain conditions don't read as
  hard failures, so the operator isn't blocked by a single missing link.
- **Variant-gating built into the gates** so variant A isn't penalized for not
  running serviceC or the value check.

## Constraints / Invariants

- Gate semantics are variant-aware; any new check must declare how it behaves in
  A vs B.
- Thresholds are named constants, never inline magic numbers.
- Parsers must tolerate missing/garbage input and return a "not present" result
  rather than throwing (field logs are messy).

## Change points

- **Tune a threshold** (freshness, check value, signal window) → the constants block
  in `status.py`, then update the golden fixtures.
- **Change a gate rule** → `compute_gates` (+ fixture in `test_status_parsers.py`).
- **Add a new health signal** → a parser in `status.py` + a collector in the profile
  ([04](04-action-profiles.md)) + wire it into `poll_node` ([05](05-orchestration-and-sequencing.md)).
- **Change the overall roll-up** → `overall_gate`.

## Open questions

- Gate state is computed each poll with no hysteresis; a flapping signal flaps the
  gate. Debounce/hold-down is a candidate if field noise proves annoying.
- The rx-log fallback ages are coarser than `links.json`; whether to visually flag
  "degraded link source" in the UI is open.
