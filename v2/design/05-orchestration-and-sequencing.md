---
noteId: "45dcacc06b0311f1b060577f73b9a94a"
tags: []
title: "Orchestration & Sequencing"
order: 5
summary: "The engine — how actions fan out across the fleet and how variant-aware bring-up / tear-down order is enforced."

---

# Orchestration & Sequencing

## Overview

`core/orchestrator.py` is the engine. It turns operator intent ("bring node1 up",
"deploy the fleet") into the right ordered sequence of rendered, synthesized
commands run over pooled SSH connections — recording and broadcasting every step.
It owns the **variant-aware sequences**, the **fan-out** across nodes, the
**connection pool**, and the **health poller**. The order is not incidental: it
encodes hard invariants about how the node daemons must come up.

## From intent to command

```
run_action(node, role, action)
  → Fleet.params(node)               # derive params for current variant  [03]
  → profile.render_action(params)    # fill {param} templates             [04]
  → _dispatch by kind:
       exec          → ssh.execute(cmd)
       transfer      → transfer.run_transfer(...)                         [06]
       daemon*       → supervisor.{start,stop,status}_cmd → ssh.execute   [06]
  → ActionResult (ok, output, cmd)
  → audit to session events.jsonl + broadcast over SocketIO             [09][10]
```

Single-node actions go through `run_action`; `run_action_silent` is the same
without the broadcast (used by the poller's internal checks).

## Fan-out across the fleet

`fan_out(role, action, node_names)` runs one action across many nodes concurrently
with a `ThreadPoolExecutor` capped at `MAX_FANOUT = 10`. The fleet-level sequences
(`*_fleet`) layer a small **stagger** between node starts so ten serviceA launches
don't all fire simultaneously — a thundering-herd guard.

## Variant-aware sequences

```
DEPLOY    (per node) : rsync serviceB + serviceA  [+ build serviceA if requested]
BRING-UP  (variant A): serviceA_start ─(healthy?)─► serviceB_start
BRING-UP  (variant B): serviceC_start@roleB ─►(healthy?)─► serviceA_start ─►(healthy?)─► serviceB_start
TEAR-DOWN (A)        : serviceB_stop ─► serviceA_stop
TEAR-DOWN (B)        : serviceB_stop ─► serviceA_stop ─► serviceC_stop@roleB
```

Each arrow is a gated step: the next daemon only starts once the previous one
reports healthy. Tear-down is the reverse of bring-up. The fleet variants
(`bring_up_fleet` / `tear_down_fleet` / `deploy_fleet`) apply these per node with
the configured stagger (tear-down / deploy use `stagger=0`).

## The invariants (do not weaken)

- **serviceA before serviceB** — serviceB depends on serviceA already listening; if
  serviceA isn't up first, serviceB has nothing to attach to.
- **serviceC before serviceA** in variant B — serviceA relies on the roleB transport
  being up.
- **Identical `$ID`** across serviceA / serviceB / serviceC on a node — single source
  is `node.id` (see [03](03-fleet-and-variants.md)).
- **Variant is per-node** (principle **P1**) — each sequence reads
  `fleet.node_variant(node)`, so a mixed-variant fleet brings each node up by its own
  variant. Any cross-node coordination constraint is the operator's job, not enforced.

## Concurrency model

- **Per-node lock** (`_node_locks`): a node can't run two sequences at once —
  bring-up and tear-down on the same node are serialized.
- **Status lock** (`_status_lock`): the in-memory status map is guarded against the
  poller and action threads racing.
- **Fan-out pool**: cross-node parallelism is bounded by `MAX_FANOUT`; per-node
  ordering is always preserved within a sequence.

## Health polling

`poll_node` runs the role collectors, feeds the parsers, builds a `NodeStatus` and
computes the gates ([07](07-health-and-gates.md)); `poll_all` covers the fleet and
`start_polling` runs it on an interval. Gate transitions emit `gate_changed`; new
status emits `node_status` (see [10](10-web-ui-and-realtime.md)). `--no-poll`
disables the loop for manual control.

## Key decisions

- **The engine, not the UI, owns ordering.** The browser asks for "bring up"; the
  correct, variant-specific, gated sequence is decided here so it can't be bypassed.
- **Health-gated steps, not fixed sleeps.** Each step waits for the previous daemon
  to report up rather than guessing a delay.
- **Bounded concurrency + stagger** rather than unbounded fan-out, to protect the
  shared resources and the nodes.

## Constraints / Invariants

- Any new sequence must keep the per-node lock and respect the ordering rules above.
- Sequencing logic stays in the orchestrator; don't push it into routes or
  templates.

## Change points

- **Add / reorder a sequence** → the `bring_up` / `tear_down` pattern in
  `orchestrator.py` (keep the node lock + ordering), then `tests/test_orchestrator.py`.
- **Tune fleet stagger** → `defaults.stagger` in `fleet.yaml`.
- **Change max parallelism** → `MAX_FANOUT` in `orchestrator.py`.
- **Change the poll interval / disable polling** → poller setup / `--no-poll`.

## Open questions

- No automatic rollback: a half-up sequence is left as-is and surfaced via gates,
  relying on the operator + audit log (principle **P6**). Worth revisiting if
  partial bring-ups become common.
- Stagger is a single fleet-wide value; an adaptive stagger keyed on observed load
  is unexplored.
