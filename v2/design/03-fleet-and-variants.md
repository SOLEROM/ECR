---
noteId: "26e19d306b0311f1b060577f73b9a94a"
tags: []
title: "Fleet Model & Variants"
order: 3
summary: "The node inventory, the two per-node variants, and how per-node parameters are derived."

---

# Fleet Model & Variants

## Overview

This is the domain model. A **fleet** is an ordered set of **nodes**; each node is
one remote target. Each node carries its own runtime **variant** (A or B — a fleet
can run mixed variants by group) and the fleet has a single **algorithm** name. From
a node's small inventory record plus **its** variant, `core/fleet.py` **derives**
every concrete parameter the rest of the system needs — host addresses, a variant
address, a launcher name, a variant flag. Nothing downstream hand-types those; they
all come from `Fleet.params(node)`, which reads the node's variant via
`Fleet.node_variant(node)`.

A **variant** is abstract: it just selects a config-driven parameter set
(`defaults.variants` in the inventory). The template ships A and B as placeholders;
re-map them to whatever two operating profiles your nodes need.

## The inventory (`fleet/fleet.yaml`)

The inventory is the single source of truth for what's in the field. Each node
declares only its irreducible facts:

```yaml
fleet:
  name: example-fleet
  defaults: { variant: A, algo: default, roleA_user: user, roleB_user: root,
              key_file: ~/.ssh/id_rsa, deploy_root: /srv/ccfleet/roleA, stagger: 0.5,
              roleB_host_suffix: ".2",
              ssh_opts: "-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5" }
  nodes:
    - { name: node1, id: 1, host: 10.0.0.101, subnet: 10.1.1 }
    - { name: node2, id: 2, host: 10.0.0.102, subnet: 10.1.2 }
    # … up to ~10 nodes
```

A node is `{name, id, host, subnet}` (+ optional per-node `variant` and `algo`).
Everything else is a default or a derived value. `fleet_from_dict` validates the
inventory on load: duplicate id, duplicate name, empty fleet, missing required
field, and invalid default variant are all rejected with a clear error. Because the
inventory is operator-editable from the **Config** page (**P8**) and its fields are
substituted into shell commands, identifiers are also **shape-checked**: `name`
(node + fleet) must match `NAME_RE = ^[A-Za-z0-9_-]+$` and `host` / `subnet` must
match `HOST_RE = ^[A-Za-z0-9_.:-]+$` — so a fat-fingered `host: "h; rm -rf /"` is
rejected at load, not run. See [12 — Security](12-security-and-operations.md).

## Selection groups (`fleet.groups`)

An optional `groups:` block names operator-defined subsets of the fleet, purely for
**selection** on the dashboard's *Select* line (next to All / Clear):

```yaml
fleet:
  nodes: [ … ]
  groups:
    group1: [node1, node2, node3]   # custom label → list of node names
    group2: [node4, node5]
```

Each entry becomes a button that selects exactly its nodes (Shift-click to add to
the current selection); actions and fleet commands then apply to that selection.
Group names are **display-only** — they reach the UI and audit, never a shell, so
they may contain spaces. `fleet_from_dict` validates that every member is a real
node (a typo → a line-friendly error on the Config page) and the parsed list is
exposed as `Fleet.groups_as_list()` (served by `GET /api/fleet`, rendered
client-side so a Config-page edit + reload updates the buttons with no template
change). This is config-over-code (**P8**): the operator defines groups from the
**Config** page with no code edit.

## The two hosts per node

Each node is two hosts:

- **roleA host** — the primary, directly reachable host at `host`
  (`roleA_user@10.0.0.10N`). Runs `serviceA` and `serviceB`. See
  [04 — Profiles](04-action-profiles.md).
- **roleB host** — the `<subnet><roleB_host_suffix>` (default `.2`), reached
  **through roleA as a jump-host** (`roleB_user@<subnet>.2`). Runs `serviceC` in
  variant B only.

## Variants A and B

Variant is **per-node** (principle **P1**) — each node has its own A/B toggle on its
card/detail, so a fleet can run mixed variants by group. Keeping any *coordinating*
group on a single variant is the operator's responsibility (audit-only, P6), **not
enforced** by the app. Variant is per-node runtime state on `Fleet.node_variants`
(default from `defaults.variant` or a node's configured `variant`), validated
against `VARIANTS = ("A", "B")`.

A variant selects a parameter set from the `defaults.variants` block in the
inventory — `addr` / `launcher` / `flag` — so nothing about a variant is hard-coded:

| Concern | Variant A | Variant B |
|---|---|---|
| `VAR_ADDR` (from `variants.<v>.addr`) | fixed address (e.g. `10.0.0.255`) | per-node `<subnet>.255` (the `{SUBNET}` token is replaced) |
| `VAR_LAUNCHER` (from `variants.<v>.launcher`) | `variantA.run` | `variantB.run` |
| `VAR_FLAG` (from `variants.<v>.flag`) | `""` | `--variant-flag` |
| roleB `serviceC` | not used | required, started **before** serviceA |
| Extra GATE | — | GATE C (a variant-B-only value check) applies |

## Parameter derivation (`Fleet.params(node, variant=None)`)

This one pure function is the only place derived values are computed. Given a node
and (optionally) an explicit variant override, it returns the full param dict used
to render profile actions:

```
ID         = str(node.id)            HOST_A     = node.host
SUBNET     = node.subnet             HOST_B     = <subnet><roleB_host_suffix>
VAR_ADDR     = variants.<v>.addr  ({SUBNET} replaced per node)
VAR_LAUNCHER = variants.<v>.launcher
VAR_FLAG     = variants.<v>.flag
ALGO       = node.algo or defaults.algo      VARIANT = current/overridden variant
DEPLOY_ROOT, roleA_user, roleB_user, key_file, ssh_opts  ← from defaults
```

## Key decisions

- **Derive, don't store.** Addresses and flags that follow mechanically from
  `id` / `subnet` / `variant` are computed, so the inventory can't drift out of
  sync.
- **Per-node variant as runtime state** (P1) — `Fleet.node_variants`, each node
  toggled independently. The operator owns any cross-node coordination constraint;
  the app does not enforce it.
- **`algo` is validated** against `ALGO_RE = ^[A-Za-z0-9_-]+$` in `set_algo`,
  because it is substituted into a remote shell command. **Node/fleet `name` and
  `host`/`subnet` are validated the same way at load** (`NAME_RE`/`HOST_RE`) — every
  identifier that reaches a shell is a bare token. See
  [12 — Security](12-security-and-operations.md).

## Constraints / Invariants

- `id` is unique and is the single source of `$ID` for serviceA, serviceB **and**
  serviceC on a node — they must match (see [05](05-orchestration-and-sequencing.md)).
- Derived params must only ever be read from `Fleet.params`; never recompute a
  `VAR_ADDR` / `VAR_LAUNCHER` / `HOST_B` elsewhere.
- Variant is one of `A` / `B`; `set_node_variant` rejects anything else.

## Change points

- **Add / remove a node** → edit `fleet/fleet.yaml` (no code change).
- **Add / rename a selection group** → edit `fleet.groups` from the Config page
  (no code change); `tests/test_fleet.py` covers parsing/validation.
- **Add a derived parameter** → `Node` / `params()` in `core/fleet.py`, plus a
  `tests/test_fleet.py` case.
- **Re-map what a variant means** (its address, launcher, flag) → the
  `defaults.variants` block in `fleet.yaml` from the Config page; nothing is
  hard-coded.

## Open questions

- A third variant could be added; if needed, `VARIANTS` and the `defaults.variants`
  block both grow.
- Per-node `deploy_root` overrides are not modeled (only a fleet default); add to
  `Node` if a heterogeneous fleet appears.
