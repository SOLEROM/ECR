# Plan 2 — Config-driven gates (the **Gates** Config root)

> **Goal.** Make the health gates **operator-editable config**, not hard-coded logic.
> Add a new Config-page tree node **Gates** backed by a `gates/` directory; each `*.yaml`
> there is **one gate** that declares *what to run, where to run it, and how the result
> maps to a color*. This is the same config-over-code move (P8) we already made for the
> connectivity / state LEDs — the **Gates** subsystem is modeled directly on the existing
> **States** subsystem (`core/states.py` + `core/state_monitor.py` + the `networks/`
> "States" root). After this, an operator retunes a gate's host, process list, command,
> thresholds and refresh interval from the browser — validated → hot-reloaded → audited —
> with no code change.
>
> **This is the *template*.** The four gates stay generic placeholders (A reach · B proc ·
> C check · D link), the tokens stay generic (`roleA`/`roleB`, `serviceA`/`serviceB`/
> `serviceC`, variants `A`/`B`). Nothing app-specific lands here; a fork refills the four
> YAMLs for its own world. The **engine** (`core/gates_config.py`), the orchestrator/UI
> wiring and the Compiler emitter are template-level — forks inherit config-driven gates.

---

## 1. The gates, as the operator will define them (generic template defaults)

| Gate | Label | Kind | Runs on | Variants | What it does |
|---|---|---|---|---|---|
| **A** | reach | `reach` | base → roleA | A, B | Is the node reachable? Config carries the **method** (ssh\|ping), **host** and **timeout**. Reachable → green, no answer → red. |
| **B** | proc | `process` | roleA | A, B | A **list of processes** that must be running; each flagged **mandatory or not** and optionally **variant-scoped** (`serviceC` is B-only). Mandatory down → red; only an optional down → yellow; all up → green. |
| **C** | check | `metric` | roleA | B | Runs a **command** that prints a value; color by **thresholds** (`value`). The YAML defines the command, how to read the fields, the level→color rules, and the refresh **interval/timeout**. |
| **D** | link | `metric` | roleA | A, B | Same engine, a different command/field (a peer/link count). Shows the `metric` kind in the default variant. |

The operator edits four files — `gates/gateA.yaml … gateD.yaml` — and may add more later (a
5th gate is just a 5th file). The starter files double as schema-by-example (§5).

---

## 2. Why model it on **States** (and what's different)

The States bar already does 90% of what we need and is the proven template to copy:

- **Pure schema + registry** — `core/networks.py` / `core/states.py` parse+validate a dir of
  YAML into an in-memory model, reloaded **in place** so a Config save takes effect with no
  restart (`StateRegistry.reload`).
- **I/O monitor** — `core/state_monitor.py` polls each indicator in parallel, maps the result
  to a **named color**, broadcasts `states_status`, and fires an **`on_change`** hook wired
  to a session-log line on every color flip (audit, P6).
- **Config root** — `core/config_store.py` registers `states` → `networks/` with
  validate→backup→write→reload→audit; the Config page renders it as a tree node.
- **Mock** — under `--mock`/`--dry-run` the monitor reports the healthy color without touching
  I/O; `--no-local-commands` neutralizes shell-running indicators.

**Two things gates need that States doesn't:**

1. **Gates are per-node**, not base-station-global. A gate is evaluated for *every* node, with
   the node's `{param}` substitutions, and runs over the **ConnectionPool** (`roleA` direct,
   `roleB` via the jump-host) — or locally (`base`) for the reach ping. So the evaluator lives
   in the **orchestrator's existing poll loop** (it already owns the pool + per-node locks),
   not in a standalone monitor thread.
2. **Gates fold into a per-node card color** and the **`GATE_CHANGED` audit event** that
   already exists (`Orchestrator._publish_status`). We keep that rollup + change detection —
   only the *source* of the gate map changes (registry-driven instead of
   `domain.gates.compute_gates`).

---

## 3. Current state (what we're replacing)

- **Logic is hard-coded** in `domain/gates.py`: parsers (`parse_links`, `parse_check`,
  `parse_servicec_stats`, `parse_probe_a/b`), `compute_gates(ns)` (the A–D rules), the
  thresholds (`LINK_FRESH_MS`, `CHECK_GOOD`, …) and the `mock ↔ status` **string contract**
  constants (`CHECK_TAG`, `PROBE_A_READY`, …).
- **The fold** is `core/status.py::build_status` → `NodeStatus` + `overall_gate`, with a
  back-compat `__getattr__` shim re-exporting the `domain.gates` names.
- **Collection** is bespoke in `core/orchestrator.py::poll_node`: a hard-coded sequence of
  `_status_of`/`_collect`/`_exec_text` calls assembling a fixed `raw` dict, fed by the
  `collectors:` blocks in `profiles/*.yaml` and the probe actions. Poll cadence is a
  hard-coded `poll_interval = 3.0`.
- **Mock** mirrors all of the above in `domain/mock_rules.py` (the producer half of the
  string contract).

**Net:** every gate change today requires a code edit, a paired `mock_rules.py` edit, and a
spec edit + recompile. The point of this plan is to collapse that to *editing a YAML in the
browser*.

---

## 4. Target architecture

```
                 gates/gateA.yaml … gateD.yaml   ← operator-editable (Config: "Gates" root)
                            │  (validate → backup → write → reload → audit, like States)
                            ▼
core/gates_config.py  (PURE)  GateSpec · gate_file_from_dict · GateRegistry(dir).reload()
                              field-extraction + level-evaluation + color→severity
                            │
core/orchestrator.py  (I/O)  evaluate_gates(node):  for each due GateSpec →
   reach   → role connect / ping (cached per tick)   run via the right transport,
   process → check list over roleA/roleB (pool)      render {param}, parse, color
   metric  → run cmd over roleA/roleB (pool/local)   → GateResult{key,color,state,detail,fields}
                            │
   _publish_status(ns)  → GATE_CHANGED audit on change (kept) + per-flip session-log line
                            │                          + broadcast_node_status / broadcast_gate
web (UI)  gate cells render the named color + detail; metric pills re-sourced from gate fields;
          /api/gates exposes the registry metas (key/label/kind/on) for client-side rendering
```

**Purity split is preserved:** all the logic that can be wrong (schema parse, field
extraction, condition evaluation, color/severity mapping, variant gating) is **pure**
(`core/gates_config.py`, unit-tested with no network); the transport (connect / SSH exec /
local exec / ping) is the thin I/O shell in the orchestrator, injectable for tests and
short-circuited under `--mock`.

**Severity == the old gate vocabulary.** Each `GateResult` carries both a named `color`
(green/yellow/red/blue/purple/orange/gray) *and* a `state` (= severity: `ok`/`warn`/`fail`/
`na`). Keeping `state` means `core/status.py::overall_gate`, the card `ovr-*` rollup, the
Compiler acceptance gate (`compiler/gate.py` reads `gates.A.state == "ok"`) and existing
tests are unchanged in behavior — configs only choose colors, the engine derives severity:

```
green → ok      yellow|orange → warn      red → fail      gray → na      blue|purple → ok
```

---

## 5. The four starter gate files (schema-by-example)

One gate per file under a `gate:` block; the registry loads `gates/*.yaml` in `order` then
filename order. Keys are bare tokens (reuse `networks.KEY_RE`); every string reaches the UI
via `textContent` (XSS).

**`gates/gateA.yaml`** — reach:
```yaml
gate:
  key: A
  label: reach
  kind: reach
  on: roleA                # the role whose reachability this gate reports
  method: ssh              # ssh = control-plane truth (also short-circuits role gates); or "ping"
  host: "{HOST_A}"         # ping target when method: ping (per-node param)
  timeout: 5
  interval: 5
  colors: { up: green, down: red }
  hint: node reachable over the control plane
```

**`gates/gateB.yaml`** — proc (process list, mandatory + variant flags):
```yaml
gate:
  key: B
  label: proc
  kind: process
  on: roleA
  timeout: 8
  interval: 5
  check: "pgrep -f {pattern} >/dev/null 2>&1"   # exit 0 ⇒ running
  processes:
    - { name: serviceA, pattern: serviceA, mandatory: true }
    - { name: serviceB, pattern: serviceB, mandatory: true }
    - { name: serviceC, pattern: serviceC, mandatory: true, variants: [B] }
  colors: { all_up: green, optional_down: yellow, mandatory_down: red }
  hint: required processes running
```

**`gates/gateC.yaml`** — metric (value check, variant B only):
```yaml
gate:
  key: C
  label: check
  kind: metric
  on: roleA
  variants: [B]
  cmd: "cat /tmp/ccflet/check.value 2>/dev/null"
  timeout: 6
  interval: 5
  parse: regex                                   # or: json
  fields:
    - { name: value, pattern: 'value\s*[=:]\s*(\d+)', type: int }
  detail: "value={value}"
  levels:                                         # first match wins
    - { when: { value: ">=3" }, color: green  }
    - { when: { value: ">=1" }, color: yellow }
    - { default: true,          color: red, detail: "no/low check" }
  mock: { up_when: serviceB, healthy: { value: 3 } }
  hint: a per-node value check (variant B)
```

**`gates/gateD.yaml`** — metric (peer/link count, both variants):
```yaml
gate:
  key: D
  label: link
  kind: metric
  on: roleA
  cmd: "cat /tmp/ccflet/links.count 2>/dev/null"
  timeout: 6
  interval: 5
  parse: regex
  fields:
    - { name: peers, pattern: '(\d+)', type: int }
  detail: "{peers} peer(s)"
  levels:
    - { when: { peers: ">=1" }, color: green  }
    - { default: true,          color: yellow, detail: "no peers" }
  mock: { up_when: serviceA, healthy: { peers: 2 } }
  hint: peer/link liveness
```

### Schema summary (`core/gates_config.py`)

**Common keys (all kinds):** `key`, `label`, `kind` (`reach|process|metric`), `on`
(`base|roleA|roleB`, default by kind), `variants` (list; default all — a node whose current
variant isn't listed ⇒ gate `na`), `timeout`, `interval`, `hint`, `order`, `mock`
(simulate-only block, ignored by the engine, read by `domain/mock_rules.gate_mock`).

**`reach`:** `method` (`ssh` default | `ping`), `host` (`{param}`-aware, validated as a host
token), `colors.{up,down}`.

**`process`:** `check` (command template with `{pattern}`/`{name}`; default `pgrep -f
{pattern}`, exit 0 ⇒ up), `processes[]` (`{name, pattern?, mandatory, variants?}`),
`colors.{all_up, optional_down, mandatory_down}`.

**`metric`:** `cmd`, `parse` (`regex`|`json`), `fields[]` (`{name, pattern|key, type:
int|float|bool}`), `levels[]` (ordered `{when:{field:cond}, color, detail?}` + a final
`{default: true, color}`), `detail` template. Conditions: `">=n"`/`"<=n"`/`">n"`/`"<n"`/
`"==v"`, `"a..b"` ranges, bool `true/false`, literal `==value`.

---

## 6. Polling, transport & reachability short-circuit (orchestrator)

`poll_node` becomes **registry-driven** (`evaluate_gates`):

1. **Per-tick reachability, once per (node, role).** Before running role gates, connect each
   needed role once and cache it for the tick. This is the control-plane truth and
   **short-circuits** dependent gates: if `roleA` won't connect, every `on: roleA` gate
   resolves immediately to `fail` instead of stacking SSH connect timeouts. GATE A's `reach`
   result is this connect (`method: ssh`) or a base-station ping (`method: ping`).
2. **Evaluate each due gate.** For each `GateSpec` whose `variants` includes the node's
   current variant and whose `interval` has elapsed (tracked in `_gate_last_run[(node,key)]`),
   evaluate via the kind's runner; not-due gates keep their cached `GateResult`. Gates run in
   parallel per node (`ThreadPoolExecutor`, like `StateMonitor.poll_once`).
3. **Assemble + publish.** Build `NodeStatus{node, variant, reachable_*, gates:{key:result}}`,
   store under `_status_lock`, call the **unchanged** `_publish_status` → `GATE_CHANGED` audit
   on change + per-flip session-log line + `broadcast_gate`/`broadcast_node_status`.
4. **Cadence.** The poll tick becomes `gates.poll_interval` = `min(gate.interval)` floored at
   ~1s; the hard-coded `poll_interval = 3.0` is removed. `--no-poll` still disables the loop.

Runners (`role connect`, ping, SSH exec, local exec) are injectable so this stays unit-testable
with no network. Under `--mock`/`--dry-run` the orchestrator never touches the wire (§8).

---

## 7. Mock & dry-run

We keep `--mock` meaningful **without** re-introducing a per-command string contract. Under
mock the gate engine does **not** match arbitrary operator commands; it asks a small,
kind-aware hook `domain/mock_rules.py::gate_mock(state, node, spec) → result` that keys off
the **simulated world**, not the command string:

- `reach` → `state.is_reachable(node)` (so `set_offline` makes GATE A red).
- `process` → for each entry, `state.is_up(node, entry.name)` (the process names are the
  simulated daemon keys: `serviceA/B/C`). **This preserves the "bring-up flips proc green"
  demo** — the most valuable mock behavior.
- `metric` → a healthy sample from the gate's `mock.healthy` block when `mock.up_when` (a
  simulated daemon) is up and the node is reachable, else an empty reading (→ the `default`
  level, typically red). So C goes green only after `serviceB` is up; D after `serviceA`.

The orchestrator detects mock by the pooled client carrying `.state` (a `MockFleetState`),
exactly like `_do_transfer` already does. `--dry-run` with the real factory short-circuits to
the healthy color (a preview, like States `simulate`). `on: base`/`reach: ping` are the
higher-blast-radius local path: echo-only under mock/dry-run, neutral (gray) under
`--no-local-commands`.

**Big win:** the `domain/gates.py ↔ domain/mock_rules.py` *gate* string contract dissolves —
the mock no longer parses gate text. The supervisor/sequence command routing and the **live
log producers** (`check_lines`/`servicec_stats`/`links_*` feeding the node-detail log panes
via `stream_kind`/`stream_line`/`domain_read`) **stay** — they are the demo's log content, not
a gate parser. `domain/gates.py` shrinks to just that log/command vocabulary.

---

## 8. UI changes

- **Gate cells become color-driven.** `dashboard.html` / `node.html` build the gate cells
  from a new `GET /api/gates` (`[{key,label,kind,on}]`, mirroring `/api/states` and
  `/api/commands`) instead of `identity.gates`. Each cell takes `.gate.c-<color>` from
  `ns.gates[key].color` and shows `ns.gates[key].detail`; the card `ovr-*` rollup uses
  `state` (severity, unchanged logic). The named palette is shared with States
  (`.led.c-<color>` → `.gate.c-<color>`).
- **Metric pills re-sourced.** The process gate exposes per-process up/down → render those as
  pills; the metric gates expose their `fields` → a compact pill each. The old fixed
  `serviceA/B/C`-up + `links`/`check`/`signal` pills (which read the bespoke `NodeStatus`
  shape) are rebuilt from the gate results.
- **Live rebuild on edit.** A `gates_changed` SocketIO event (like `commands_changed`) is
  emitted on a Config reload of the `gates` scope, so open dashboards rebuild their gate cells
  without a page reload.
- **GUI parts:** keep `data-guipart` ids on the gate containers (`guiPart25/26`, `guiPart45`);
  update `web/templates/GUIPARTS.md`.

---

## 9. Config page, hot-reload & audit

- **New root** in `core/config_store.py`: a `gates` root → `gates/` (`.yaml`/`.yml`,
  `KIND_GATES`, scope `gates`) added in `default_roots(...)`; new `KIND_GATES` branch in
  `validate_text` calling `gates_config.gate_file_from_dict` (a non-coder gets a line-numbered
  error). `default_roots` gains a `gates_dir=` keyword (kept backward-compatible).
- **CLI:** new `--gates-dir` flag (default `gates`, resolved against the app root at boot),
  threaded through `app.py` into the registry + the config store, alongside `--states-dir`.
- **Hot-reload:** `CCFletApp.reload_config("gates")` → `GateRegistry.reload()` **in place**
  (the orchestrator holds the same ref, like `StateRegistry`/`Fleet`), then a re-poll. Reuse
  the validate→backup→write→reload→audit path verbatim; emit `CONFIG_SAVED` + `CONFIG_RELOADED`.
- **Gate transitions → session log (P6).** The existing `GATE_CHANGED` event lands in
  `events.jsonl` on any gate-map change. We additionally drop a human-readable session-log
  line on each gate's **color flip** (the same `on_change` pattern `StateMonitor` uses for
  `STATE_CHANGED`) — "GATE C on d3 went green→red (no/low check)". The first reading after
  boot/reload is the baseline and emits nothing (no spam).

---

## 10. Consequences & out of scope (template)

- **`NodeStatus` is reshaped** to `{node, variant, reachable_roleA, reachable_roleB,
  gates:{key:result}}`. The bespoke fields (`links`, `check1/2`, `servicec_stats`, `serviceA/
  B/C` status dicts, probes) leave the container — they were the inputs to the old hard-coded
  gates. The UI re-sources what it shows from the gate results (§8).
- **The old parsers + `compute_gates` + `build_status` + the `status.py` back-compat shim are
  removed.** `core/status.py` keeps the `OK/WARN/FAIL/NA` vocabulary, `NodeStatus` and
  `overall_gate` (the generic rollup). `domain/gates.py` keeps only the **mock log/command
  vocabulary** the live-log producers still import.
- **Old gate behaviors that don't map onto reach/process/metric are dropped from gating** (the
  mesh peer-age freshness, the serviceC signal window, the v2v probes). A fork that needs them
  re-expresses them as a `metric` gate or a States LED. The collectors/probes in `profiles/`
  and their mock producers stay only as far as the **live log panes** still use them.
- **Profiles `collectors:` are no longer the gate feed.** A gate's command lives in its own
  YAML (`cmd`/`check`), run directly by the orchestrator. The `collectors:` blocks remain for
  any non-gate use (and the demo log content) but no longer drive status.

---

## 11. Compiler / spec write-back (this repo also hosts the Rebuild system)

Per `CLAUDE_rebuild.md`, the per-app slice must land in the spec so a clean `./compile.sh`
reproduces it. The gate **engine + UI + orchestrator wiring are template-level** (generic,
not the per-app slice) — they are not in the manifest, like `core/states.py`. The per-app
slice is the four `gates/*.yaml`.

- **Gate YAMLs become spec-emitted (full rewrite), replacing the patch step.** Add an
  `emit_gates` to `compiler/build.py` that writes `gates/gate*.yaml` from
  `system/layer3.subparts/gate-*.yaml` (full rewrite, modeled on `emit_networks`); register
  `gates/*.yaml` in `manifest.owned`. Retire `patch_gate_thresholds` (the threshold/contract
  constants it patched no longer drive gating; the surviving mock-vocabulary constants need no
  spec patching). Add the `gates` part type to `compiler/catalog.py`.
- **Acceptance gate unchanged.** `compiler/gate.py` reads `gates.A.state`/`gates.B.state` —
  still present because `GateResult` keeps `state` (§4). No change needed.
- **Identity labels.** Gate labels now live in the gate YAMLs (`label:`); the UI reads
  `/api/gates`. Keep `identity.gates` as a thin fallback (and the existing `emit_identity`
  path) so older templates render; the registry is the source of truth.

> **Scope note for this change.** The runtime capability (engine + config root + orchestrator
> + mock + UI + hot-reload + audit + tests) is the deliverable and lands first. The
> `emit_gates` spec write-back is a focused follow-up (it touches only the Compiler + spec) and
> is tracked in §12 P7; nothing in the running app depends on it.

---

## 12. Testing

- **New pure suite `tests/test_gates_config.py`:** schema parse/validate (each kind + bad
  inputs → line-numbered `ValueError`), `{param}` rendering, field extraction (regex + json,
  int/float/bool), level evaluation (first-match, ranges, bool, default), color→severity,
  variant gating (`na`), registry load/reload-in-place + cross-file key clash.
- **`tests/test_config_store.py`:** add the `gates` root — valid save, invalid (bad color, bad
  `when`, traversal/extension), revert.
- **`tests/test_orchestrator.py`:** registry-driven `evaluate_gates` with the mock — reach
  short-circuit (offline → A fail, role gates fail), per-gate `interval` (not-due keeps cached
  result), parallel eval, `GATE_CHANGED` fires only on change, gate-flip → session-log line.
  Replace the `ns.links["count"]`/bespoke-shape assertions with gate-field checks.
- **Replace `tests/test_status_parsers.py`** (golden fixtures for the removed parsers) — fold
  the useful shapes into `test_gates_config.py`.
- **`tests/test_routes.py`:** add `/api/gates` (lists registry metas) + a gates hot-reload edit.
- **Live `--mock` boot (mandatory, CLAUDE.md §7):** all gates light; **bring-up flips GATE B
  green**; `set_offline` → GATE A red; metric gates green once their `up_when` daemon is up.
  `--dry-run` prints/echoes the local gate path.

---

## 13. Phased checklist

- [ ] **P1 — Pure engine.** `core/gates_config.py` (`GateSpec`, `gate_from_dict`,
      `gate_file_from_dict`, `GateRegistry`, field/level/color logic) + `tests/test_gates_config.py`.
- [ ] **P2 — Config root + files.** `gates` root in `config_store` + `--gates-dir` in `app.py`;
      ship the four `gates/*.yaml` + `gates/README.md`; the Config tree node appears.
- [ ] **P3 — Orchestrator integration.** Rewrite `poll_node` → `evaluate_gates`; pool reuse +
      ping/local runners + per-tick reachability short-circuit; per-gate `interval`; keep
      `_publish_status` audit + broadcast; `reload_gates` hook; remove `poll_interval = 3.0`.
- [ ] **P4 — Mock & simulate.** `domain/mock_rules.py::gate_mock`; verify `--mock` fidelity.
- [ ] **P5 — UI.** `/api/gates`; gate cells render color/detail; re-source pills;
      `gates_changed` live rebuild; `GUIPARTS.md`; `.gate.c-<color>` CSS.
- [ ] **P6 — Cleanup.** Remove the parsers/`compute_gates`/`build_status`/`status.py` shim;
      slim `domain/gates.py` to the mock vocabulary; reshape `NodeStatus`; refresh
      `design/07-health-and-gates.md` + `plan1.md §4`.
- [ ] **P7 — Spec write-back (follow-up).** `emit_gates` + manifest entries; retire
      `patch_gate_thresholds`; `compiler/catalog.py` gates part; run the acceptance gate.
