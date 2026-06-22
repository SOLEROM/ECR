# ccFleet — a generic SSH Command & Control template

`ccFleet` is a small, self-contained **web app for operating a fleet of remote nodes
over SSH**: bring the fleet **up**, **watch** its health, and bring it **down** — with
**everything recorded**. It is a clean, domain-free **project template** — fill in the
config (which nodes, which services, which checks) and adapt the code; nothing in here
is tied to any particular kind of node or workload.

It is built around one idea: **config over code**. The operator who *runs* ccFleet can
change the fleet inventory, the per-role command profiles, the catalog of triggerable
commands, and the connectivity checks entirely from the **Config** page in the
browser — validated, hot-reloaded, and audited — without touching source.

```bash
./run.sh --mock          # simulated fleet, no hardware → http://127.0.0.1:5000
./run.sh                 # real fleet (edit fleet/fleet.yaml first)
./run.sh --dry-run       # print the SSH/transfer commands, run nothing
.venv/bin/python -m pytest   # 181 tests, no network
```

`run.sh` creates the `.venv` and installs deps on first run. (Deps are not in the
system Python — PEP 668.) See **CLAUDE.md** for the full engineering brief and
**design/** (also served read-only on the **Help** page) for the deeper docs.

## What it does

- **Dashboard** — a card per node with four health **gates** (A reach · B proc ·
  C check · D link), live service pills, and per-node Deploy / Bring-up / Tear-down.
- **Node detail** — single-node actions + live `tail -F` log panes (xterm).
- **Sessions** — every action and its result is appended to an append-only audit
  (`events.jsonl`); each session dir is ZIP-exportable.
- **Config** — edit the operator-editable YAML/scripts in the browser; saves are
  validated → backed up → hot-reloaded → audited.
- **Help** — the `design/` docs, rendered.

## The model

A node has two **roles** and runs one of two per-node **variants** (A / B):

- **roleA** — the primary host, reached directly over SSH.
- **roleB** — a secondary host reached *through* roleA as an SSH jump-host; used
  only in **variant B**.

Each role has a **profile** (`profiles/roleA.yaml`, `profiles/roleB.yaml`) — a
parameterized catalog of actions (`transfer` / `exec` / `daemon` / `daemon_stop` /
`daemon_status`), status **collectors**, and tailable **logs**. The example profiles
run three demo daemons: **serviceA** + **serviceB** on roleA, and **serviceC** on
roleB (variant B only).

`fleet/fleet.yaml` is the single source of truth for the inventory. Per node:
`name`, `id`, `host` (roleA), `subnet`; an optional per-node `variant`. Fleet
`defaults` carry the SSH users, deploy root, stagger, the `roleB_host_suffix`, and a
**`variants`** block — the config-driven parameter set each variant selects
(`addr` / `launcher` / `flag`, surfaced to commands as `VAR_ADDR` / `VAR_LAUNCHER` /
`VAR_FLAG`). `core/fleet.py::Fleet.params()` derives the full substitution dict per
node — you never hand-type derived values.

```yaml
fleet:
  name: example-fleet
  defaults:
    variant: A
    variants:
      A: { addr: "10.0.0.255", launcher: "variantA.run", flag: "" }
      B: { addr: "{SUBNET}.255", launcher: "variantB.run", flag: "--variant-flag" }
  nodes:
    - { name: node1, id: 1, host: 10.0.0.101, subnet: 10.1.1 }
```

## Sequencing (the heart)

`core/orchestrator.py` runs **variant-aware, ordered sequences** and enforces the
ordering invariants in code:

```
DEPLOY   (per node) : rsync serviceB + serviceA  [+ build serviceA]
BRING-UP (variant A): serviceA_start ─(healthy?)─► serviceB_start
BRING-UP (variant B): serviceC_start@roleB ─►(healthy?)─► serviceA_start ─►(healthy?)─► serviceB_start
TEAR-DOWN(A)        : serviceB_stop ─► serviceA_stop
TEAR-DOWN(B)        : serviceB_stop ─► serviceA_stop ─► serviceC_stop@roleB
```

Fleet variants fan the per-node sequence across the selection with a small
configurable `stagger`.

## Layout

```
app.py              composition root + CCFletApp facade + CLI
core/               pure logic (fleet, profiles, supervisor, status, transfer,
                    commands, networks) + I/O shells (ssh_client, orchestrator,
                    streaming, sync, events, storage, net_monitor, mock_ssh)
web/                routes.py + templates/ + static/ (css, vendored xterm/socket.io)
fleet/ profiles/ commands/ networks/   the operator-editable config (the Config page)
design/             the Help-page docs
scripts/            base-station CLI helpers (not the command catalog)
tests/              pure-logic unit tests + a mock-backed integration suite
```

## Make it yours

This is a template — rename and refill. Common starting points:

| You want to… | Edit |
|---|---|
| change the fleet (nodes, hosts, variants) | `fleet/fleet.yaml` (from the Config page) |
| change what each role runs | `profiles/roleA.yaml` / `roleB.yaml` |
| add a triggerable button | `commands/commands_{host,roleA,roleB}.yaml` (+ a `*.sh`) |
| change the top-bar connectivity LEDs | `networks/networks.yaml` |
| change a health gate or threshold | `core/status.py` |
| restyle / relabel a UI part | find its `guiPartNN` id (see `web/templates/GUIPARTS.md`) |
| swap the logo | `web/static/logo/svg/mark.svg` + the inline mark in `base.html` |

No auth/RBAC is built in: the posture is a closed LAN, trusted operators, bind to a
chosen interface (`--public` for `0.0.0.0`), and **audit everything**.
