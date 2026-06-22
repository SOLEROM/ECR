---
noteId: "360f43c06b0311f1b060577f73b9a94a"
tags: []
title: "Action Profiles"
order: 4
summary: "The per-role catalogs of parameterized actions, collectors and logs, and how they render to commands."

---

# Action Profiles

## Overview

A **profile** is a declarative catalog, one per node role, of the things `ccFleet`
can do to that role: **actions** (transfer / exec / daemon control), **collectors**
(periodic health probes) and **logs** (tailable files). Profiles are data
(`profiles/*.yaml`), not code — adding a capability is usually a YAML edit plus a
mock update, not an engine change. `core/profiles.py` loads them, and renders the
`{param}` placeholders against a node's derived params from
[03 — Fleet](03-fleet-and-variants.md).

## Roles

- **`roleA`** (`profiles/roleA.yaml`) — the primary, directly reachable host.
  Deploys + builds serviceA/serviceB, starts/stops/statuses both daemons, collects
  link liveness and a value check.
- **`roleB`** (`profiles/roleB.yaml`) — the secondary host, **reached via the roleA
  jump-host** (`connection.via`). Variant B only: `serviceC`, plus probe A / probe B
  status checks and the serviceC-stats collector.

## Action kinds

`ACTION_KINDS = (transfer, exec, daemon, daemon_stop, daemon_status)`:

| Kind | Purpose | Handled by |
|---|---|---|
| `transfer` | bulk copy code to the node (rsync / scp) | [06 — transfer](06-supervision-and-deployment.md) |
| `exec` | run a one-shot command, capture output | orchestrator `_dispatch` |
| `daemon` | start a long-lived process (detached or systemd) | [06 — supervisor](06-supervision-and-deployment.md) |
| `daemon_stop` | stop it (by pidfile / match / systemctl) | supervisor |
| `daemon_status` | is it up? (pidfile → pgrep → systemctl) | supervisor |

A `daemon` action carries a `name` (pidfile key), the `command` to run, optional
`prefer_systemd` (e.g. serviceA), `after` (ordering hint), and stop/status actions
carry a `match` substring for `pgrep`.

## Parameter substitution

`substitute()` replaces `{param}` tokens in any action or connection field from the
node's param dict. Example, `roleA.serviceA_start` rendered for node1 in variant A:

```
template : cd {DEPLOY_ROOT}/serviceA && ID={ID} ADDR={VAR_ADDR} ./{VAR_LAUNCHER} tcp
rendered : cd /srv/ccfleet/roleA/serviceA && ID=1 ADDR=10.0.0.255 ./variantA.run tcp
```

Switch the node to variant B and the *same template* renders `./variantB.run`, the
`<subnet>.255` address, and `--variant-flag` on serviceB — because the params
changed, not the profile.

## Connections & the jump-host

Each profile has a `connection` block, also `{param}`-rendered. The roleB profile's
adds a `via`:

```yaml
connection: { user: "{roleB_user}", host: "{HOST_B}",
              via: "{roleA_user}@{HOST_A}", port: 22, timeout: 5 }
```

`ConnectionConfig.from_profile_connection` parses `via` into the jump-host hop —
paramiko opens a `direct-tcpip` channel through roleA to reach roleB. See
[08 — Connectivity](08-connectivity-and-streaming.md).

## Collectors & logs

- **Collectors** are `{command, interval, parser}` — the orchestrator runs the
  command periodically and feeds the output to the named parser in
  [07 — status](07-health-and-gates.md). e.g. roleA `links` →
  `cat /run/serviceA/links.json … || tail … /tmp/serviceA.rx` → `link` parser.
- **Logs** map a short name to a path (e.g. `serviceB: /tmp/ccflet/serviceB.log`)
  for live tailing in [08](08-connectivity-and-streaming.md).

## Key decisions

- **Capabilities are data.** Putting actions in YAML keeps the engine generic and
  makes the catalog reviewable without reading Python.
- **One template, both variants.** Variant-specific behavior lives in the *params*,
  so a profile action is written once and renders correctly for A and B.
- **`prefer_systemd` per action**, not global — serviceA prefers a unit where one is
  installed; everything else is detached + pidfile (principle **P5**).

## Constraints / Invariants

- Every `{param}` used in a profile must be produced by `Fleet.params`, or the
  render fails — keep the two in step.
- A daemon's `name` is also its pidfile key (`/tmp/ccflet/<name>.pid`); keep it
  stable, the mock and supervisor both key off it.
- Values rendered into commands must be safe tokens — see
  [12 — Security](12-security-and-operations.md).

## Change points

- **Add an action** (new daemon, new exec) → add a block to
  `profiles/{roleA,roleB}.yaml`; if it's a new *kind*, extend
  `supervisor.py`/`_dispatch`. Then teach `mock_ssh.py` and add a test.
- **Add a collector** → profile `collectors:` + a parser in `status.py`.
- **Change a deploy source / destination path** → the `transfer` action's
  `src` / `dst`.

## Open questions

- Profiles are static YAML loaded at boot but **hot-reloaded** on a Config-page save
  (`ProfileManager.invalidate` + `Orchestrator.reload_profiles`); a richer in-UI
  profile editor beyond the raw-text Config page is unscoped.
- Action-level timeouts exist but there is no per-action retry policy yet.
