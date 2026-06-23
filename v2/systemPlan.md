# systemPlan.md — a system that builds a system

> **What this document is.** ccFleet (this repo) is a generic SSH command-&-control
> *template*. This plan describes the **meta-system** that turns a short, human wish list
> into a finished, working ccFleet app — and lets us **rebuild** it whenever the wish list
> changes. Working name for the meta-system: **the Compiler** (a.k.a. *Forge*).
>
> This is a **plan**, not code. It is the contract the Compiler and its file formats must
> satisfy. The source code remains the truth; where this disagrees with code, fix one.

---

## 1. The need (why a system that builds a system)

We will create **many** apps from this one template (weather fleets, kiosk fleets, camera
fleets, …). Today, adapting the template by hand means: rename placeholders everywhere,
write the real `fleet`/`profiles`/`commands`/`networks` config, **and** edit real Python
(health gates, parsers, bring-up/tear-down order, the `--mock` simulator). That is slow,
error-prone, and drifts.

We want instead to:

1. **Describe** a real app in a few small, human-friendly files (a "wish list").
2. **Build** it into a complete, *verified-working* app with one command.
3. **Rebuild** it predictably whenever the description changes — without losing work and
   without hand-editing generated code.
4. **Reuse** the same flow for every future app, and **pull template improvements** into
   apps already built.

So we are building a **compiler**: *wish list → working app*, with editable intermediate
steps and an automatic correctness check.

### Requirements (what the meta-system must do)

- **R1 — Low floor, high ceiling.** A one-paragraph dream must produce a working app; a
  fully detailed spec must control every button, gate, and command. Same flow, your choice
  of depth.
- **R2 — Defaults from the template.** You describe only what differs; everything unspecified
  inherits a sensible template default. No over-defining.
- **R3 — Editable checkpoints.** Every guess the LLM makes becomes a file you can read and
  correct *before* it flows downstream. No single opaque leap from idea to app.
- **R4 — Choose what's runtime-editable.** For each item we decide: **`live`** (lands in the
  app's editable Config page, operator can change it on a running app) or **`frozen`** (baked
  into code at build time).
- **R5 — Spec is the source of truth.** The app is a *build artifact*. You change behavior by
  editing the wish list and rebuilding — never by hand-editing generated output.
- **R6 — Verified builds.** A build is only "done" when the template's own `pytest` +
  `--mock` boot + `--dry-run` pass. A build can never silently ship a broken app.
- **R7 — Safe rebuilds.** Re-running any stage must not clobber your edits in other stages.
- **R8 — Maintain & extend.** A clear, repeatable process to change a built app (live config
  vs rebuild) and to extend it (new button/gate/service), plus a way to pull template upgrades.

---

## 2. Why this is feasible (the code/config split)

The template already follows **P8 — config over code**, so a large part of "define a system"
is *already* editable data. The Compiler's job is to fill that data deterministically and to
**generate the small remainder that is genuinely code**.

| Today it is… | Surfaces | Compiler treatment |
|---|---|---|
| **Pure data** (operator-editable, hot-reloaded) | `fleet/fleet.yaml`, `profiles/{roleA,roleB}.yaml`, `commands/commands_{host,roleA,roleB}.yaml` (+ `*.sh`), `networks/networks.yaml` | **Deterministic fill** from the spec |
| **Domain code** (must be adapted per app) | `core/status.py` (parsers + GATE rules + thresholds), `core/orchestrator.py` (bring-up/tear-down/deploy order + guards), `core/mock_ssh.py` (command matcher), labels in `core/` + `web/templates/*.html` | **LLM codegen** into isolated, verified files |

This split is the backbone: **most of the system is data; only gates/parsers/sequences/mock
are code** — and those will be isolated so they regenerate cleanly (see §6).

---

## 3. Locked decisions

From our design discussion:

| # | Decision | Choice |
|---|---|---|
| D1 | **Topology** | **Standalone fork per app.** Each app is a full copy of the template with its wish list (`system/`) alongside; the Compiler edits the copy. |
| D2 | **Domain logic** | **Generated into isolated files + verified.** Gates/parsers/sequences/mock-rules land in a `domain/` package; `pytest` + `--mock` is the acceptance gate. |
| D3 | **Source of truth** | **The spec wins; generated files are disposable.** Edit `system/` → rebuild. Don't hand-edit generated output. |
| D4 | **Spec format** | **Layered files + a prose brief**, refined into the **3-stage pipeline** below. |

Consequence of D1+D2+D3 — **the boundary that makes rebuilds safe:**

- The **generic engine** (`core/` minus `domain/`, the I/O shells, the Flask/SocketIO
  scaffold) stays ≈ the template and is *not* spec-derived. This keeps a fork close enough
  to the template that upstream improvements can be pulled (see §10c).
- The **spec-derived artifacts** (the four config roots + the `domain/` pack + fenced label
  regions) are owned by the Compiler, tracked in a manifest, and freely regenerated.
- **Labels are data; identifiers stay stable.** Operator-facing names (what shows on cards,
  buttons, gates) come from the spec. Internal **brand tokens are load-bearing — keep them**:
  `ccflet`, `CCFlet`, `CCFLET_`, `/tmp/ccflet`, `X-CCFlet-User` (per `CLAUDE.md §8`).

---

## 4. The three layers (dream → params → sub-parts)

The wish list lives in a **`system/`** folder inside each app. It has three layers, coarse
to fine. **Each layer is the editable output of the previous one** (see §5):

```
LAYER 1  layer1.dream.md         YOU WRITE — free text: what the app should do
   │  (Compiler: distill)
LAYER 2  layer2.params.yaml       DRAFTED, you edit — global facts (names, nodes, gates)
   │  (Compiler: expand)
LAYER 3  layer3.subparts/*.yaml   DRAFTED, you edit — one file per UI sub-part, full knobs
   │  (Compiler: build + verify)
APP                               the finished, tested app
```

### Layer 1 — the dream (`layer1.dream.md`)
Free-format prose. The only thing you must write from scratch. States the mission, what each
role/service does, when a node is healthy, which buttons you want, and any *don'ts*.
*Example (WeatherCtl): five stations; each runs a collector + uploader; a sensorpod runs a
calibrator first in "live" mode; healthy = reachable + both up + humidity 20–80% + upload
<60s; cards get Calibrate + Download-24h; no reboot button.*

### Layer 2 — the params (`layer2.params.yaml`)
The few **global facts** true for the whole app — drafted by the Compiler from the dream,
then corrected by you:
```yaml
app:
  name: WeatherCtl
  node:     { count: 5, represents: "a weather station in the field" }
  roles:    { roleA: station, roleB: sensorpod }     # roleB may be null (single-host nodes)
  services: { serviceA: collector, serviceB: uploader, serviceC: calibrator }
  variants: { A: "dry calibration", B: "live calibration" }   # may be a single variant
  gates:    { A: reach, B: procs, C: humidity, D: uplink }     # a gate may be dropped
  defaults: { roleA_user: pi, deploy_root: /srv/weather, stagger: 0.5 }
  nodes_seed: placeholder        # real hosts/subnets are LIVE data, edited in-app later
```
This layer flexes the template's shape: nullable `roleB`, fewer/more variants, dropped gates.

### Layer 3 — the sub-parts (`layer3.subparts/*.yaml`)
One YAML per region of the app, each a **patch** on a template default (`extends:` +
`add:`/`remove:`/overrides), drafted by the Compiler from the params and then fine-tuned:
```yaml
# layer3.subparts/node-actions.yaml
extends: guiPart-node-actions      # a part id from system/catalog.md
layout: grid
add:
  - { id: calibrate,   label: "Calibrate now",    on: station, scope: node, script: calibrate.sh, timeout: 30, mode: live }
  - { id: download24h, label: "Download last 24h", on: station, scope: node, run: "tar czf - {DEPLOY_ROOT}/data/today", mode: live }
remove: [reboot]
```
```yaml
# layer3.subparts/gate-c.yaml
extends: gate.C
label: humidity
applies_to_variant: B
collector: humidity
parse: "regex \\[HUM\\]\\s+(\\d+)%"
good:  "20 <= value <= 80"
mode: frozen
```
A **`catalog.md`** lists every overridable sub-part and its default; a `scaffold` command
dumps any part's current default as a ready-to-edit Layer-3 file (so you edit a filled file,
not a blank one). **Skip a sub-part → you get the template default** (R2).

### Supporting files in `system/`
- `system/scripts/*.sh` — scripts referenced by `script:` items.
- `system/assets/*` — logo / brand.
- `system/build.yaml` — the pipeline control + per-stage status (see §5).
- `system/catalog.md` — the list of overridable sub-parts.

---

## 5. The `mode:` flag — config vs code (R4)

Every Layer-3 item carries one flag that decides **where the Compiler writes it**:

| `mode:` | Lands in | Editable on a running app? |
|---|---|---|
| `live` | the editable config roots (`fleet`/`profiles`/`commands`/`networks` YAML) | **Yes** — via the app's Config page (validated → hot-reload → audited) |
| `frozen` | `domain/` code or a fenced template region | **No** — change the spec and rebuild |
| *(omitted)* | the template's default mode for that item type | per default |

So the `mode:` flag is literally the router between "operator can change this at runtime" and
"this is fixed at build time." Gate rules default `frozen` (they're code); command buttons
default `live` (they're catalog data).

---

## 6. The Compiler — a staged pipeline with editable checkpoints

The Compiler is **three stages**, each an LLM-assisted transform whose output is an
**editable intermediate artifact** (an IR you can read and correct):

| Stage | Transform | Reads | Writes | Nature |
|---|---|---|---|---|
| **distill** | dream → params | `layer1.dream.md` | `layer2.params.yaml` | LLM-creative |
| **expand** | params → sub-parts | `layer2.params.yaml` (+ dream for context) | `layer3.subparts/*.yaml` | LLM-creative |
| **build** | sub-parts → app | `layer3.subparts/*` (+ params) | config roots + `domain/` + fenced labels | deterministic where possible; LLM only for code domain bits |

**Creativity concentrates early (distill/expand); precision + verification concentrate late
(build).** By the build stage, the inputs are precise YAML, so most output is a deterministic
data transform; the LLM is used only for the irreducibly-code parts (parsers/gates/sequences/
mock), and every build is checked by the gate in §7.

### Re-run any range (R7)
`system/build.yaml` records each stage, its artifact, and a **status**:
`approved` (you wrote/blessed it) · `draft` (Compiler-made) · `stale` (an upstream artifact
changed). You rebuild a **range**:
```bash
./compile.sh --from dream    --to app        # full build
./compile.sh --from params   --to app        # I edited params → redraft sub-parts + build
./compile.sh --from subparts --to app        # I edited sub-parts → just rebuild the app
./compile.sh --only params                   # redraft params from dream, then STOP to edit
./compile.sh scaffold gate.D                 # dump a part's default as an editable L3 file
```
**Rules that protect your work:**
- **Range-only.** A rebuild touches only `--from..--to`; other layers are untouched.
- **Upstream re-runs stop.** Re-running an *earlier* stage regenerates it and halts (no
  auto-cascade over your downstream edits) — you re-review, then let it flow on.
- **Approved lock.** An `approved` artifact is never overwritten without `--force`.
- **Staleness.** Editing an artifact marks downstream stages `stale` until re-run.
- **Precedence.** `layer3 > layer2 > layer1 (prose) > template default`. Prose only fills
  gaps; anything pinned in L2/L3 is law.

### Inferred-back transparency
Each stage's output *is* the inference, made into an editable file — so "what the LLM
understood" is always visible and correctable. The build stage also writes a **compile
report**: what was specified vs inferred, plus TODOs wherever a *detailed* request was
underspecified (never a silent guess).

---

## 7. The acceptance gate (R6) — the correctness oracle

A build is **not "done" until it passes**, reusing the template's own discipline:

1. `pytest` — the pure-logic + mock-backed suites (adapted to the app's `domain/`).
2. `app.py --mock` — boots the simulated fleet and confirms it lights up (the
   `mock_ssh.py ↔ status.py` string contract holds).
3. `app.py --dry-run` — prints synthesized commands so they can be eyeballed.

If any fail, the Compiler **fixes and retries** in a bounded loop; if it can't go green it
**exits non-zero and ships nothing**. This is what makes "rebuild" trustworthy: the output is
always a *working* app, not merely a plausible one.

---

## 8. Template prerequisite — extract `domain/` once (Phase 0)

To make D2/D3 real, the **template itself** needs a one-time refactor so the per-app domain
logic is isolated and loadable:

- `core/status.py` parsers + GATE rules + thresholds → **`domain/gates.py`** (loaded by the
  engine; the generic folding stays in `core/status.py`).
- `orchestrator` bring-up/tear-down/deploy step lists + guards → **`domain/sequences.yaml`**
  (the engine reads and runs them; ordering invariants enforced generically).
- `mock_ssh` domain command rules → **`domain/mock_rules.py`**.
- names/labels → **`domain/identity.*`** + fenced `<!-- GEN:identity --> … <!-- /GEN -->`
  regions in templates / `base.html` brand / `app.py` defaults.

After Phase 0, "generate domain into isolated files" is whole-file ownership = trivially safe
to regenerate, and the generic engine stays close to the template for upgrades (§10c).

---

## 9. Repository layout

```
ccfleet-template/                  the upstream template (this repo) — generic, never an app
  core/  web/  profiles/  …        the generic engine (+ domain/ after Phase 0, with defaults)
  system/                          DEMO wish list (the starting example for a fork)
  compile.sh                       the Compiler entry point
  design/  plan1.md  CLAUDE.md     the contract the Compiler is given as context

apps/
  weatherctl/                      a forked app (full copy of the template)
    system/                        ← THE ONLY THING HAND-AUTHORED
      layer1.dream.md
      layer2.params.yaml           (draft → you edit)
      layer3.subparts/*.yaml       (draft → you edit)
      scripts/  assets/  build.yaml  catalog.md
    domain/                        GENERATED (gates.py, sequences.yaml, mock_rules.py, identity.*)
    fleet/ profiles/ commands/ networks/   GENERATED config roots (the `live` items)
    .compiler-manifest.json        every Compiler-owned path (for safe clobber + `--check`)
    (core/ web/ … = the generic engine, ≈ template)
```

`.compiler-manifest.json` lets a rebuild clobber exactly the owned paths, and a
`compile.sh --check` warns if a human edited a generated file (a smell under D3).

---

## 10. The three lifecycle processes

### 10a. Build a NEW app
1. **Fork** the template into `apps/<name>/`.
2. **Write the dream** — `system/layer1.dream.md` (a paragraph is enough).
3. **Distill** — `./compile.sh --only params`; **review/edit** `layer2.params.yaml`
   (fix names, node count, which gates exist), mark it `approved`.
4. **Expand** — `./compile.sh --from params --to subparts`; **review/edit** the
   `layer3.subparts/*.yaml` (add/remove buttons, set gate rules, set each item's `mode:`),
   approve.
5. **Build** — `./compile.sh --from subparts --to app`. The gate (§7) must go green.
6. **Run** — `app.py --mock` to click through; then point `fleet.yaml` at real hosts and run
   live on one node before fanning out.

> Shortcut: `./compile.sh --from dream --to app` does it all in one shot; the staged form is
> for when you want to steer each checkpoint.

### 10b. MAINTAIN a built app
Two paths, by who's changing what:
- **Operator, at runtime (no rebuild).** Anything `live` — fleet inventory, profiles,
  command buttons, network LEDs, selection groups — is edited on the app's **Config page**
  (validated → hot-reloaded → audited). This is the day-to-day path and needs no Compiler.
- **Developer, structural change (rebuild).** Anything `frozen` or structural (gate rules,
  sequencing, a new daemon kind, renamed roles) → edit `system/` and rebuild the affected
  range (`--from subparts --to app`, etc.). The gate guarantees it still works.

Decision rule: *can an operator safely change it on a live fleet?* → make it `live`. *Is it
logic that could be wrong?* → keep it `frozen` and rebuild.

### 10c. EXTEND a built app (and adopt template upgrades)
- **Add a button** → add an item to a `layer3.subparts/*-actions.yaml` (`mode: live`),
  `--from subparts --to app`. (Or, if `live`, just add it on the Config page.)
- **Add/redefine a gate** → edit/scaffold a `layer3.subparts/gate-*.yaml` (`mode: frozen`),
  rebuild. Update the mock so `--mock` recognizes any new probe (the Compiler does this; the
  gate verifies it).
- **Add a service / change sequencing** → params (new service name) + a
  `layer3.subparts/sequence-*.yaml`; rebuild. Ordering invariants stay enforced generically.
- **Pull a template upgrade into an existing app** → because the generic engine in a fork
  stays ≈ the template (§8), merge the template's `core/`/`web/` changes, then re-run
  `./compile.sh --from subparts --to app` to re-emit the app's spec-derived artifacts on top.
  The gate confirms the upgrade didn't break the app.

---

## 11. Roadmap to build the Compiler

| Phase | Deliverable | Notes |
|---|---|---|
| **0** | `domain/` extraction in the template (+ loaders, fences, defaults) | The one real prerequisite (§8) |
| **1** | `system/` schema + `SPEC.md` + `catalog.md` + demos | Doubles as the Compiler's instructions |
| **2** | `compile.sh` staged pipeline (distill/expand/build) + the acceptance gate + retry loop | The core of the meta-system |
| **3** | `build.yaml` status model, `.compiler-manifest.json`, `--from/--to/--only`, `--check`, `scaffold`, compile report | Safe, predictable rebuilds (R7) |
| **4** *(optional)* | interactive "interview" front-end that writes `layer1.dream.md` for you; a small **Build** page in-app | Nice-to-have |

---

## 12. Open questions / decisions still pending

- **Variant/gate generality.** The template assumes A/B and a B-only GATE C in places. Phase 0
  must generalize to *N* variants (incl. 1) and droppable gates (the KioskOps demo needs this).
- **Identifier rename vs label-only.** Default: rename operator-facing **labels** via the
  spec; keep brand tokens. Confirm whether structural keys (role/service/gate ids) should be
  renamed in a fork too (cosmetic; possible since all spec-derived files regenerate together).
- **Where forks live.** Default assumption: `apps/<name>/` siblings. Alternatives: separate
  repos or git worktrees.
- **Determinism of the build stage.** How much of config-root generation is a pure transform
  vs LLM-assisted; aim to maximize the pure-transform share for reproducibility.
- **Concurrency on edits / optimistic-concurrency** on the in-app Config page (last-write-wins
  today) — inherited from the template's own open question.

---

## 13. One-line summary

**Write a short dream → the Compiler drafts editable `params`, then editable `sub-parts`, then
builds a verified app; the spec is the source of truth, `mode:` decides runtime-editable vs
baked-in, and you rebuild any stage range safely whenever the dream changes.**
