# ccFleet — the template application (part 1 of 2)

> **This repo has two parts** (see the short root [`README.md`](README.md)). This is the
> doc index for **part 1 — the ccFleet template app itself**. Part 2, the **Rebuild
> system** that forks this template and builds new apps from a wish list, is documented in
> [`README_rebuild.md`](README_rebuild.md). Engineering brief: [`CLAUDE.md`](CLAUDE.md).

`ccFleet` is a small, self-contained **web app for operating a fleet of remote nodes over
SSH**: bring the fleet **up**, **watch** its health, and bring it **down** — with
**everything recorded**. It is a clean, domain-free template — fill in the config (which
nodes, which services, which checks) and adapt the `domain/` pack; nothing here is tied to
a particular kind of node or workload.

It is built around one idea: **config over code**. The operator who *runs* ccFleet can
change the fleet inventory, the per-role command profiles, the catalog of triggerable
commands, and the connectivity checks entirely from the **Config** page in the browser —
validated, hot-reloaded, and audited — without touching source.

```bash
./run.sh --mock          # simulated fleet, no hardware → http://127.0.0.1:5000
./run.sh                 # real fleet (edit fleet/fleet.yaml first)
./run.sh --dry-run       # print the SSH/transfer commands, run nothing
.venv/bin/python -m pytest   # 201 tests, no network
```

`run.sh` creates the `.venv` and installs deps on first run (deps are not in the system
Python — PEP 668). Deeper docs:

- [`CLAUDE.md`](CLAUDE.md) — the full engineering brief (module map, conventions, gotchas).
- [`plan1.md`](plan1.md) — architecture overview + the design principles P1–P8.
- [`design/`](design) — the per-subsystem reference (also served on the **Help** page).
- [`scripts/README.md`](scripts/README.md) — base-station CLI helpers (not the catalog).
- [`web/templates/GUIPARTS.md`](web/templates/GUIPARTS.md) — the `guiPartNN` UI-part index.

## What it does

- **Dashboard** — a card per node with four health **gates** (A reach · B proc ·
  C check · D link), live service pills, and per-node Deploy / Bring-up / Tear-down.
- **Node detail** — single-node actions + live `tail -F` log panes (xterm).
- **Sessions** — every action and result is appended to an append-only audit
  (`events.jsonl`); each session dir is ZIP-exportable.
- **Config** — edit the operator-editable YAML/scripts in the browser; saves are
  validated → backed up → hot-reloaded → audited.
- **Help** — the `design/` docs, rendered.

## The model

A node has two **roles** and runs one of two per-node **variants** (A / B):

- **roleA** — the primary host, reached directly over SSH.
- **roleB** — a secondary host reached *through* roleA as an SSH jump-host; variant B only.

Each role has a **profile** (`profiles/roleA.yaml`, `profiles/roleB.yaml`) — a
parameterized catalog of actions (`transfer` / `exec` / `daemon` / `daemon_stop` /
`daemon_status`), status **collectors**, and tailable **logs**. The example profiles run
three demo daemons: **serviceA** + **serviceB** on roleA, **serviceC** on roleB (variant B).

`fleet/fleet.yaml` is the single source of truth for the inventory;
`core/fleet.py::Fleet.params()` derives the full per-node substitution dict (you never
hand-type derived values).

## Sequencing (the heart)

`core/orchestrator.py` runs **variant-aware, ordered sequences** read from
`domain/sequences.yaml`; `core/sequences.py` enforces the ordering invariants generically:

```
DEPLOY   (per node) : rsync serviceB + serviceA  [+ build serviceA]
BRING-UP (variant A): serviceA_start ─(healthy?)─► serviceB_start
BRING-UP (variant B): serviceC_start@roleB ─►(healthy?)─► serviceA_start ─►(healthy?)─► serviceB_start
TEAR-DOWN(A)        : serviceB_stop ─► serviceA_stop
TEAR-DOWN(B)        : serviceB_stop ─► serviceA_stop ─► serviceC_stop@roleB
```

## The `domain/` pack (per-app logic)

The slice of behavior that changes per app is isolated in `domain/` (so the Rebuild system
can regenerate it): `gates.py` (parsers + GATE rules + thresholds), `mock_rules.py` (the
`--mock` producer side of the string contract), `sequences.yaml` (the order above), and
`identity.py` (operator-facing labels). The generic engine (`core/`, `web/`) stays ≈ the
template. See `CLAUDE.md §4`.

## Layout

```
app.py              composition root + CCFletApp facade + CLI
core/               generic engine: pure logic (fleet, profiles, supervisor, status,
                    sequences, transfer, commands, networks) + I/O shells (ssh_client,
                    orchestrator, streaming, sync, events, storage, net_monitor, mock_ssh)
domain/             per-app spec-derived logic (gates, mock_rules, sequences, identity)
web/                routes.py + templates/ + static/ (css, vendored xterm/socket.io)
fleet/ profiles/ commands/ networks/   the operator-editable config (the Config page)
design/             the Help-page docs (part-1 subsystem reference)
scripts/            base-station CLI helpers (not the command catalog)
tests/              pure-logic unit tests + a mock-backed integration suite
─── part 2 (the Rebuild system; see README_rebuild.md) ───
compiler/  compile.sh  system/        the Compiler + the wish list it builds from
```

## Make it yours

Two ways, depending on scale:

- **Operator, at runtime** — change anything `live` (fleet, profiles, command buttons,
  network LEDs) from the **Config** page. No code, no restart.
- **Developer, structural** — edit the source/`domain/` directly **here in the template**,
  or — for a real fork — describe it in a `system/` wish list and let the Rebuild system
  build it (see [`README_rebuild.md`](README_rebuild.md)).

| You want to… | Edit (template) |
|---|---|
| change the fleet (nodes, hosts, variants) | `fleet/fleet.yaml` (from the Config page) |
| change what each role runs | `profiles/roleA.yaml` / `roleB.yaml` |
| add a triggerable button | `commands/commands_{host,roleA,roleB}.yaml` (+ a `*.sh`) |
| change the top-bar connectivity LEDs | `networks/networks.yaml` |
| change a health gate or threshold | `domain/gates.py` (keep `domain/mock_rules.py` in sync) |
| change bring-up/tear-down order | `domain/sequences.yaml` |
| relabel the app (name / brand / gate labels) | `domain/identity.py` |
| restyle / relabel a UI part | find its `guiPartNN` id (see `web/templates/GUIPARTS.md`) |
| swap the logo | `web/static/logo/svg/mark.svg` + the inline mark in `base.html` |

No auth/RBAC is built in: the posture is a closed LAN, trusted operators, bind to a chosen
interface (`--public` for `0.0.0.0`), and **audit everything**.
