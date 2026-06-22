# CLAUDE.md — working on `ccFleet` (a generic fleet Command & Control template)

This is the engineering brief for the **`ccFleet`** stack, which lives **at the project
root** (this directory — `ECR/v2/`; `app.py`, `core/`, `web/`, … are direct children).
Read it before changing code. It explains *what this is, how it's shaped, where to make
changes, and what must not break.* It complements — does not repeat — two docs:

- `plan1.md` — the architecture overview + the design principles (P1–P8).
- `design/*.md` — the per-subsystem reference (also served on the **Help** page).

> The source code is the truth. When a fact here disagrees with the code, fix one of
> them — don't guess.

---

## 1. What `ccFleet` is (and is not)

**Mission:** bring a fleet of remote nodes **up**, **watch** it, and bring it **down**
— over SSH — with **everything recorded**. `ccFleet` is **strictly an ops/control
plane**: deploy · daemon lifecycle · logs · health. The control path
`ccFleet → node` is **always SSH**.

This is a **clean, domain-free template**. The names are deliberate placeholders —
two roles (**roleA**, **roleB**), three demo services (**serviceA**, **serviceB**,
**serviceC**), a per-node **variant** (A/B), four health **gates** (A–D). Rename and
refill them for your project; nothing here assumes a particular kind of node or
workload.

**Top objective — config over code (P8):** the operator who *runs* ccFleet **cannot
edit, see, or run the code.** So everything they may need to change at runtime — the
fleet inventory, role profiles, variants, and the **catalog of triggerable commands**
— must live in **operator-editable config (YAML + scripts) reachable from the web UI**
(the **Config** page), validated before it takes effect, hot-reloaded with no restart,
and audited like any other action. When you add operator-facing logic, land it as
**config, not hard-coded behavior**. See §6b and `design/13-config-and-commands.md`.

**Out of scope (by design):** auth/RBAC (closed LAN, trusted operators, bind to a
chosen interface, audit everything); one-time provisioning (ccFleet assumes nodes are
already reachable and does the per-run loop only).

---

## 2. Design principles (constraints you must honor)

| # | Principle | What it means for the code |
|---|---|---|
| P1 | **Per-node variant** | `Fleet.node_variants` is per-node runtime state — each node has its own A/B toggle on its card/detail (no global selector). A variant selects a config-driven param set (`defaults.variants`). |
| P2 | **SSH-only control plane** | `ccFleet → node` is always SSH; ops plane only — no telemetry ingest, no out-of-band channels. |
| P3 | **Port the proven parts** | Reuse `events` / `storage` / `sync` / `ssh_client` + the Flask/SocketIO scaffold + vendored xterm. |
| P4 | **Hybrid SSH/transfer** | paramiko for exec/status/stream/jump; system `rsync` (roleA push) + `scp -O` (roleB, via jump). |
| P5 | **Detached + pidfile supervision** | `setsid nohup … >log 2>&1 </dev/null & echo $! >pid`; status via pidfile→pgrep→systemd; prefer systemd where a unit exists. |
| P6 | **Audit-only safety** | No confirm prompts, no dry-run gating in the UI. *Every* action **and** result → `events.jsonl`. The audit log is the safety net. (`--dry-run` is for dev/test.) |
| P7 | **No provisioning** | Assume nodes are reachable; do the per-run loop only. |
| P8 | **Config over code** | Fleet/profiles/variants/commands/networks are operator-editable YAML+scripts served by the **Config** page: validated → hot-reloaded → audited. New operator-facing logic is config, not code. |

---

## 3. Run / test / dev loop

Deps are **not** in the system Python (PEP 668, externally-managed). Use the venv at
**`.venv/`** in the project root (`./run.sh --mock` creates it + installs deps on
first run):

```bash
# from the project root (ECR/v2/)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # one-time
.venv/bin/python app.py --mock          # simulated fleet → http://127.0.0.1:5000
.venv/bin/python app.py                 # real fleet (edit fleet/fleet.yaml)
.venv/bin/python app.py --dry-run       # print the real SSH commands, run nothing
.venv/bin/python -m pytest                  # 181 tests, no network
.venv/bin/python -m pytest --cov=core       # coverage
```

CLI flags: `--host --public --port --fleet <yaml> --profiles-dir --commands-dir --networks <yaml> --runs-dir --mock --dry-run --no-poll --no-local-commands --debug`. `--public` binds `0.0.0.0` for the LAN (no-auth posture — §8); default bind is `127.0.0.1:5000`.

**Launching the dev server from an automated shell:** use `setsid … &` and poll with
`curl --retry`. A compound command that exits non-zero tears down the backgrounded
server's process group (a harness quirk, not a code bug).

**Always develop/verify against `--mock` first** (see §7). The mock lights the whole
dashboard green without hardware.

**Base-station housekeeping lives in `scripts/`** — standalone shell helpers you run
**from a terminal** (e.g. `scripts/clear_sessions.sh` wipes every session dir + ZIP
under `runs/`). These are **not** the operator command catalog (`commands/`, §6b):
nothing in ccFleet runs them, they're not on the UI, and they're not validated /
hot-reloaded / audited. See `scripts/README.md`.

---

## 4. Architecture & data flow

```
browser ──HTTP/WebSocket──► Flask + SocketIO (web/routes.py, core/sync.py)
                                   │
                              CCFletApp facade (app.py)  ── owns the session, variant/algo
                                   │
                              Orchestrator (core/orchestrator.py)  ◄── the engine
             ┌─────────────────────┼───────────────────────────────┐
        ConnectionPool        sequences/fan-out                 status poller
        (real | mock)         (variant-aware)                   (collectors→GATE)
             │                       │                                │
   ssh_client / mock_ssh      supervisor + transfer            status.py (pure)
   (paramiko + jump-host)     (cmd synthesis)                  events/storage/sync
```

**Module responsibilities** (`core/`):

| File | Role | Purity |
|---|---|---|
| `fleet.py` | inventory model + per-node **param derivation** (variant) | **pure** |
| `profiles.py` | action/collector/log schema + `{param}` render + `via` jump | **pure** |
| `supervisor.py` | daemon start/stop/status **command synthesis** + parse | **pure** |
| `status.py` | collector **parsers** → `NodeStatus` → **GATE A–D** | **pure** |
| `transfer.py` | rsync / scp -O command synthesis + subprocess runner | mostly pure |
| `orchestrator.py` | fan-out, **variant-aware sequences**, conn pool, poller, **`run_custom`** | I/O |
| `commands.py` | **operator command catalog** schema (`commands/commands_{host,roleA,roleB}.yaml`) + `{param}` render (P8) | **pure** |
| `networks.py` | **base-station link** schema (`networks/networks.yaml`) — the top-bar LEDs (P8) | **pure** |
| `net_monitor.py` | **ping poller** for the off-fleet links → `net_status` broadcast (sim under mock/dry) | I/O |
| `config_store.py` | **Config page** backend — path-safe read/validate/write/revert of the editable roots (P8) | mostly pure |
| `local_exec.py` | run a **local** (base-station) command as a subprocess → `CommandResult` | mostly pure |
| `ssh_client.py` | paramiko wrapper + **jump-host** + `exec_stream()` | I/O |
| `streaming.py` | live `tail -F` → SocketIO rooms → xterm | I/O |
| `mock_ssh.py` | **stateful simulated fleet** (for `--mock` and tests) | sim |
| `events.py` `storage.py` `sync.py` | audit JSONL · session dirs/ZIP · multi-op rooms | I/O |
| `docs.py` | read-only `design/` markdown tree for the **Help** page | pure |
| `result.py` | shared `CommandResult` (paramiko-free, so pure modules can import it) | pure |

`web/` = `routes.py` (REST + pages) + `templates/` (base, dashboard, node, sessions,
session_view, **config**, help; `GUIPARTS.md` indexes the `guiPartNN` markers) +
`static/` (`main.css`, `app.js`, vendored xterm + socket.io). `app.py` is the
composition root + `CCFletApp` facade + CLI.

**Pure vs I/O split is deliberate:** all the logic that can be wrong (param derivation,
sequencing order, command synthesis, parsing, gating) is pure and unit-tested with no
network. The I/O shells are thin and exercised by the `--mock` boot. Keep new logic on
the pure side when you can.

---

## 5. Data model

- **`fleet/fleet.yaml`** — the inventory. `defaults` + `nodes[{name,id,host,subnet,
  variant?,…}]`. Variant is **per-node** runtime state (`Fleet.node_variants`, default
  from `defaults.variant`); `algo` is a fleet-wide token. `core/fleet.py::Fleet.params(node)`
  derives the rest **per current variant** and is the only place that computes
  `HOST_B`/`VAR_ADDR`/`VAR_LAUNCHER`/`VAR_FLAG` — never hand-type those. The
  variant-derived values come from the operator-editable `defaults.variants` block
  (`{A,B}: {addr,launcher,flag}`; `addr` may contain `{SUBNET}`), and `HOST_B` from
  `subnet` + `roleB_host_suffix` — so nothing about a variant is hard-coded.
- **`profiles/{roleA,roleB}.yaml`** — parameterized action catalogs. Action `kind ∈
  {transfer, exec, daemon, daemon_stop, daemon_status}`. `roleB.yaml` sets
  `connection.via` → reached through the roleA jump-host (variant B only).
- **`commands/commands_{host,roleA,roleB}.yaml`** (+ `commands/*.sh`) — the operator's
  catalog of extra **triggerable commands** (P8), **split by where each runs**:
  `commands_host` → local (subprocess on the base station), `commands_roleA` /
  `commands_roleB` → remote (SSH to that role). The file supplies the `on`/`role`, so
  entries need neither. Each renders to a button: `scope: node|fleet`, `run:` inline
  **xor** `script:` (a file under `commands/`), `timeout`, `danger` (visual+audit
  emphasis only — no confirm, per P6). `CommandCatalog` merges all files; duplicate
  names across files are rejected. Edited from the **Config** page; parsed by
  `core/commands.py`.
- **`networks/networks.yaml`** — the operator's list of **off-fleet** links the base
  station watches, one **top-bar LED** each (green = reachable, red = no reply).
  `links[{key,label,host,hint}]` + `poll_interval`/`ping_timeout`.
  `core/net_monitor.py` ICMP-pings them and pushes `net_status`; **not** fleet nodes.
- **GATE A–D** (`core/status.py`, variant-gated) — A reachability · B processes ·
  C check (variant B only) · D link. Thresholds are constants at the top of
  `status.py` (`LINK_FRESH_MS`, `CHECK_GOOD`, `SERVICEC_MIN_UP`, `SIGNAL_OK_RANGE`).
- **Session** = one ops run dir under `runs/`: `manifest.json` + `events.jsonl` +
  `fleet_snapshot.yaml` + `logs/` + `artifacts/`, ZIP-exportable. No retention policy
  and **no UI delete-all** — reset the whole list from a terminal with
  `scripts/clear_sessions.sh` (run ccFleet stopped; `-n` previews, `-y` skips prompt).

---

## 6. The heart: variant-aware sequencing (`orchestrator.py`)

```
DEPLOY   (per node) : rsync serviceB + serviceA  [+ build serviceA]
BRING-UP (variant A): serviceA_start ─(healthy?)─► serviceB_start
BRING-UP (variant B): serviceC_start@roleB ─►(healthy?)─► serviceA_start ─►(healthy?)─► serviceB_start
TEAR-DOWN(A)        : serviceB_stop ─► serviceA_stop
TEAR-DOWN(B)        : serviceB_stop ─► serviceA_stop ─► serviceC_stop@roleB
```

**Invariants enforced in code — do not weaken:**
- **serviceA before serviceB** (serviceB depends on serviceA being up).
- **serviceC before serviceA** in variant B.
- identical `$ID` across the services (single source: `node.id`).
- variant is **per-node**: each sequence reads `fleet.node_variant(node)`; a fleet can
  run mixed variants by group.

Fleet variants fan the per-node sequence across the selection with a small
configurable `stagger` (avoids a thundering herd on a 10-node bring-up).

---

## 6b. Config over code (P8) — the **Config** page + custom commands

The operator can't touch source, so the logic they tune lives in editable config,
served read-write by the **Config** page (`/config`, the read-write twin of Help):

- **Editable roots** (`core/config_store.py`): `fleet/` · `profiles/` · `commands/` ·
  `networks/`. The store is **path-safe** (registered roots only, extension allow-list,
  no traversal — same discipline as `core/docs.py::safe_resolve`) and reads the tree
  fresh per request.
- **Validate → backup → write → reload → audit** on every save:
  - *validate first, never persist invalid* — fleet via `fleet.fleet_from_dict`,
    profiles via `profiles.profile_from_dict`, commands via `commands.commands_from_dict`,
    networks via `networks.networks_from_dict`; YAML errors report a line number.
  - a timestamped copy of the prior file is written to `<root>/.bak/` (Revert restores
    the newest).
  - **hot-reload, no restart:** `CCFletApp.reload_config(scope)` → `Fleet.reload_from_dict`
    (in-place: orch/factory/mock all hold the same `Fleet` ref; current variant/algo
    preserved if still valid) · `ProfileManager.invalidate` + `Orchestrator.reload_profiles`
    · `CommandCatalog.reload` · `Networks.reload_from_dict` (in-place; `NetMonitor` holds
    the same ref, then re-polls) · `MockFleetState.reload` · close the conn pool so
    changed hosts reconnect.
  - `CONFIG_SAVED` + `CONFIG_RELOADED` events → `events.jsonl` (P6).
- **Custom commands** (`core/commands.py`, `orchestrator.run_custom`): buttons are built
  **client-side** from `GET /api/commands`, so editing a `commands_*.yaml` file + reload
  changes the UI with no template edit. **Remote** commands render the node's `params`
  (bare-token-safe) and run over SSH (`scope: fleet` fans out); **local** commands run
  on the base station via `core/local_exec.py`. The UI shows a **remote (🛰) vs local
  (🖥) chip** so it's always clear where a command runs.

**Trust model (read §8 too):** the Config page can author YAML/scripts and trigger
arbitrary shell — that's RCE-as-a-feature *by design* (P8) and is only acceptable under
the existing posture: closed LAN, trusted operators, bind to a chosen interface, **audit
everything** (P6). Local exec is the higher blast-radius path — it is **echo-only under
`--mock`/`--dry-run`** and gated by `--no-local-commands`.

---

## 7. The mock backend (`core/mock_ssh.py`) — how `--mock` works

`--mock` swaps the client factory so every `(node, role)` gets a `MockSSHClient` bound
to a shared `MockFleetState`. The mock **pattern-matches the real synthesized commands**
(supervisor start/stop/status, collectors, probes, tails) against an in-memory world —
so the supervisor, orchestrator and parsers are genuinely exercised; only the wire is
faked. Bring-up flips services up → peers see each other → collectors emit fresh lines →
gates go green, exactly like a real bring-up.

**When you add or change a remote command, update the mock to recognize it** (or its
tests will pass while `--mock` shows nothing). The matcher keys off stable substrings
(e.g. `/tmp/ccflet/<name>.pid`, `setsid nohup`, `links.json`, `serviceB.log`,
`serviceC.log`, `probeA`/`probeB`). `MockFleetState.set_offline()` simulates an
unreachable node (red GATE A). **The `mock_ssh.py ↔ status.py` string contract is the
sharpest edge:** renamed log tags / probe strings / daemon keys must match on both sides
or `--mock` goes dark while unit tests still pass — verify the live `--mock` boot, not
just `pytest`.

---

## 8. Conventions & gotchas (read before editing)

- **SocketIO `async_mode='threading'`** (+ `simple-websocket`), **not gevent** — on
  purpose, to avoid monkey-patch-vs-paramiko hazards. Do not switch to gevent without
  re-validating paramiko threads.
- **`ssh_client.execute()` is lock-serialized** for the whole command. **Never** stream
  through it — live tails use `exec_stream()` on a *dedicated* channel.
- **Event format has two shapes:** on disk `events.jsonl` uses `event_type`
  (`Event.to_json`/`from_json`); the wire/UI uses `type` (`Event.to_dict`). Keep them
  separate — the dashboard JS reads `type`.
- **Shell safety:** any value substituted into a remote command must be a bare token.
  `algo` is validated against `ALGO_RE` in `Fleet.set_algo`; node/host/subnet are checked
  when the inventory loads. If you add a new user-supplied value that reaches a shell,
  validate or `shlex.quote` it.
- **Path safety:** `session_id` is a filesystem path component — `SessionManager._safe_dir`
  rejects traversal. The **Config** editor is the other path-sensitive surface: every
  read/write goes through `config_store` (registered roots, extension allow-list,
  `safe_resolve`) — never join a request-supplied path onto a config root yourself.
- **Config-as-code (P8) is the deliberate exception to "validate untrusted shell":** the
  Config page lets a *trusted operator* author YAML/scripts and commands that run arbitrary
  shell — that is the feature, fenced by the LAN-only/no-auth posture and full audit (P6).
  Still keep node-derived `{param}` values bare tokens, take **no free-form runtime args**
  into a command, and keep **local** exec echo-only in `--mock`/`--dry-run` and behind
  `--no-local-commands`.
- **Concurrency:** the status dicts and per-node sequences are lock-protected in
  `orchestrator.py` (`_status_lock`, `_node_locks`). A node can't run two sequences at
  once. Preserve this if you add sequences.
- **UI:** untrusted strings (operator notes, usernames) are rendered with `textContent`,
  never `innerHTML`. Keep it that way (XSS). The brand identifiers `ccflet` / `CCFlet` /
  `CCFLET_` / `ccflet_*` / `X-CCFlet-User` / `/tmp/ccflet` are intentional — keep them.
- **GUI parts:** every visible/interactive region carries a `guiPartNN` id +
  `data-guipart` + a comment; `web/templates/GUIPARTS.md` is the index. These are a
  template aid for finding/restyling parts — no logic depends on them.

---

## 9. Where to make common improvements

| You want to… | Touch | Then |
|---|---|---|
| add/adjust a **triggerable command** | the matching `commands/commands_{host,roleA,roleB}.yaml` (+ a `commands/*.sh`) **from the Config page** — no code | reload picks it up; the button appears |
| add an editable config root / change validation | `config_store.py` (`ROOTS`, `validate`) | `tests/test_config_store.py` |
| add/adjust a **top-bar connectivity LED** | `networks/networks.yaml` (a `links` entry) **from the Config page** — no code | reload re-polls; the LED appears |
| change how the LEDs are checked | `net_monitor.py` (`ping_once`) | `tests/test_networks.py` (inject a `pinger`) |
| change what hot-reload does | `CCFletApp.reload_config` (app.py) + the per-module `reload_*` hooks | reload test + `--mock` |
| add/adjust a node action (e.g. a new daemon) | `profiles/{roleA,roleB}.yaml` (+ `supervisor` if a new kind) | teach `mock_ssh.py` the new command; add a test |
| add a fleet field / derived param | `fleet.py` (`Node`, `params`) | `tests/test_fleet.py` |
| change a GATE rule / threshold | `status.py` (constants + `compute_gates`) | `tests/test_status_parsers.py` golden fixtures |
| add a new collector/parser | `profiles` (collector) + `status.py` (parser) + `orchestrator._collect`/`poll_node` | fixture test + the mock |
| add a sequence | `orchestrator.py` (`bring_up`/`tear_down` pattern, keep the node lock + ordering) | `tests/test_orchestrator.py` |
| add a REST endpoint / page | `web/routes.py` (+ template + `app.js`) | curl smoke + `--mock` |
| change deploy transport | `transfer.py` (cmd synthesis) | `tests/test_transfer.py` |
| relabel / restyle a UI part | find its `guiPartNN` (see `web/templates/GUIPARTS.md`) | `--mock` click-through |
| real-hardware bring-up | run without `--mock`; `_real_factory` in `app.py` builds paramiko clients (roleB via `via` jump) | start on one node, then fan out |
| **reset / wipe all ops sessions** | `scripts/clear_sessions.sh` — a terminal helper, **not** the UI/catalog | `-n` previews; run with ccFleet stopped |

**Workflow for any change:** edit → add/adjust a pure-logic test → `pytest` → boot
`--mock` and click through → if it touches commands, confirm `--dry-run` prints what
you expect.

---

## 10. Testing strategy

- `FakeSSH` (in `tests/conftest.py`) records commands + returns canned results for
  supervisor tests; the **mock backend** drives orchestrator integration tests.
- Pure-logic units carry the coverage: `fleet`, `profiles`, `status`, `supervisor`,
  `storage`, `orchestrator`, `transfer`, `commands`, `networks`, `config_store`.
- I/O shells (`ssh_client`, `sync`, `streaming`) are covered by the `--mock` boot, not
  unit tests — that's the intended tradeoff. If you make one of them non-trivial, add a
  targeted test.
- `--dry-run` is the cheap way to eyeball synthesized commands without a fleet.

---

## 11. Known limitations / backlog

- This is a **template** — the example `profiles/`, `commands/`, `fleet.yaml` and the
  three demo services (serviceA/B/C) are placeholders to fill in.
- Two auth paths (paramiko + system ssh): keep one `key_file`/`ssh_opts` source in
  `fleet.yaml`; `transfer.py` passes the same opts to `rsync -e ssh` / `scp`.
- `bring_up_fleet` stagger is configurable (`defaults.stagger`); tune for a real herd.
- No auth/RBAC (by design). Real-hardware end-to-end is the main untested path —
  exercise it on one node first.
