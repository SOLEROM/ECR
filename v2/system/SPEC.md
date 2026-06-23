# SPEC.md — the `system/` wish-list format (and the Compiler's contract)

This file defines the three-layer wish list the Compiler reads, the `mode:` flag, and
the build rules. It is both the **operator's reference** and the **Compiler's
instructions**. The companion is `catalog.md` (the overridable sub-parts) and the plan
behind it all, `../systemPlan.md`.

> One-line model: **write a short dream → the Compiler drafts editable `params`, then
> editable `sub-parts`, then builds a verified app.** `mode:` decides runtime-editable
> vs baked-in; you rebuild any stage range safely whenever the dream changes.

---

## The pipeline

```
layer1.dream.md   YOU WRITE — free text (a paragraph is enough)
   │ distill   (LLM-creative; offline heuristic fallback)
layer2.params.yaml  DRAFTED → you edit — global facts (names, nodes, gates)
   │ expand    (LLM-creative; offline heuristic fallback)
layer3.subparts/*.yaml  DRAFTED → you edit — one file per UI region, full knobs
   │ build     (deterministic data transform; LLM only for free-form gate code)
APP               config roots + domain/ + fenced labels — VERIFIED by the gate
```

Run a range with `compile.sh`:

```bash
./compile.sh new <name>                              # fork the template → apps/<name>/
./compile.sh --app apps/<name> --from dream  --to app   # full build
./compile.sh --app apps/<name> --from params --to app   # edited params → redraft + build
./compile.sh --app apps/<name> --from subparts --to app # edited sub-parts → just rebuild
./compile.sh --app apps/<name> --only params            # redraft params, then STOP to edit
./compile.sh --app apps/<name> scaffold gate-c          # dump a part default to edit
./compile.sh --app apps/<name> check                    # did a human edit generated output?
./compile.sh --app apps/<name> status                   # per-stage status
```

---

## Layer 1 — `layer1.dream.md` (free prose)

The only thing you must write from scratch. State the mission; what each role/service
does; when a node is healthy; which buttons you want; and any *don'ts*. Everything you
don't say inherits a template default (R2). See this repo's `layer1.dream.md` for the
demo.

## Layer 2 — `layer2.params.yaml` (the few global facts)

```yaml
app:
  name: WeatherCtl                 # display name; a bare token. Brand *tokens* in code stay.
  fleet_name: weather              # optional; the inventory name (defaults to slug(name))
  tagline: "Command & Control"
  brand: { lead: We, accent: ather }   # optional wordmark split (lead + accented tail)
  node: { count: 5, represents: "a weather station" }
  roles: { roleA: station, roleB: sensorpod }     # roleB may be null (single-host nodes)
  services: { serviceA: collector, serviceB: uploader, serviceC: calibrator }
  variants: { A: "dry", B: "live" }               # may be a single variant
  gates: { A: reach, B: procs, C: humidity, D: uplink }   # OMIT a key to drop that gate
  defaults: { roleA_user: pi, deploy_root: /srv/weather, stagger: 0.5, ... }
  variant_params:                  # variant-derived params (addr may contain {SUBNET})
    A: { addr: "10.0.0.255", launcher: "variantA.run", flag: "" }
    B: { addr: "{SUBNET}.255", launcher: "variantB.run", flag: "--live" }
  nodes_seed:                      # optional seed inventory (real hosts are LIVE in-app)
    - { name: node1, id: 1, host: 10.0.0.101, subnet: 10.1.1 }
  groups:                          # optional dashboard selection groups
    front: [node1, node2]
```

Keys are display **labels** (what the operator sees); the structural identifiers
(`roleA`/`serviceA`/gate `A`…) and the brand tokens (`ccflet`/`CCFlet`/`/tmp/ccflet`/…)
stay in code. Drop a gate by omitting its key.

## Layer 3 — `layer3.subparts/*.yaml` (one file per UI region)

Each file is a **patch** on a template default: `extends:` a catalog part id, then
`add:`/`remove:`/overrides. Skip a sub-part entirely → you get the template default.

```yaml
# layer3.subparts/host-actions.yaml   (→ commands/commands_host.yaml, LOCAL 🖥)
extends: commands.host
add:
  - { id: base_disk, label: "Base-station disk", group: Housekeeping, run: "df -h .", mode: live }

# layer3.subparts/networks.yaml        (→ networks/networks.yaml, top-bar LEDs)
extends: networks
links:
  - { key: link1, label: Gateway, host: 10.0.0.1, hint: "the gateway" }

# layer3.subparts/gate-c.yaml          (→ domain/gates.py thresholds, FROZEN)
extends: gate.C
thresholds: { CHECK_GOOD: 3, CHECK_FRESH_S: 1.0 }
parse: "regex \\[HUM\\]\\s+(\\d+)%"   # free-form logic → flagged TODO for review/codegen
good:  "20 <= value <= 80"
```

See `catalog.md` for every overridable part and `scaffold <part>` to dump its default.

---

## The `mode:` flag — config vs code (R4)

| `mode:` | Lands in | Editable on a running app? |
|---|---|---|
| `live`   | the editable config roots (`fleet`/`profiles`/`commands`/`networks` YAML) | **Yes** — via the Config page (validated → hot-reload → audited) |
| `frozen` | `domain/` code or a fenced template region | **No** — change the spec and rebuild |
| *(omitted)* | the template default for that item type | per default |

Command buttons default `live`; gate rules + sequences default `frozen`.

---

## Build rules (what protects your edits — R7)

- **Range-only** — a run touches only the transforms in `--from..--to`.
- **Approved lock** — a stage marked `approved` in `build.yaml` is never overwritten
  without `--force` (so an upstream re-run halts before it clobbers a blessed artifact).
- **Staleness** — running a transform marks downstream stages `stale` until re-run.
- **Precedence** — `layer3 > layer2 > layer1 (prose) > template default`. Prose only
  fills gaps; anything pinned in L2/L3 is law.
- **Verified** — `build` only succeeds when the acceptance gate is green: `pytest` +
  `app.py --mock` (the fleet lights up) + `app.py --dry-run` (commands synthesise).
  A red gate ships nothing (non-zero exit).

## What `build` emits (owned paths, tracked in `.compiler-manifest.json`)

| From the spec | Emitted file | mode |
|---|---|---|
| `app.*` names | `domain/identity.py` (+ fenced `<!-- GEN:identity -->` regions) | live (labels) |
| `app.nodes_seed`/`defaults`/`variant_params`/`groups` | `fleet/fleet.yaml` | live |
| `*-actions` sub-parts | `commands/commands_{host,roleA,roleB}.yaml` | live |
| `networks` sub-part | `networks/networks.yaml` | live |
| `sequences` sub-part | `domain/sequences.yaml` | frozen |
| `gate-*` sub-part `thresholds:` | `domain/gates.py` (patched) | frozen |

Free-form gate `parse:`/`good:` logic is **flagged as a TODO** in the compile report,
never silently guessed. `compile.sh check` warns if a human hand-edited any owned file.
