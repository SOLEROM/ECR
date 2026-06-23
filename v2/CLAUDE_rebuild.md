# CLAUDE_rebuild.md — working on the **Rebuild system** (the Compiler)

> **This repo has two parts.** This file is the engineering brief for **part 2 — the
> Rebuild system**: the staged pipeline (`compiler/` + `compile.sh`) that turns a short
> `system/` wish list into a complete, verified ccFleet app and **rebuilds** it whenever
> the wish list changes. **Part 1 — the template application** has its own brief,
> **`CLAUDE.md`**. User-facing format docs live in `system/SPEC.md` + `system/catalog.md`;
> the design rationale is `systemPlan.md`. Read those for *what the formats are*; read
> this for *how the Compiler is built and where to change it.*

> The source code is the truth. When a fact here disagrees with the code, fix one.

---

## 1. What the Rebuild system is (and is not)

**Mission:** *wish list → working app*, with editable intermediate checkpoints and an
automatic correctness check, so we can spin up **many** apps from one template and
**rebuild** any of them predictably. It is **not** part of the running app — it is a
build-time tool that edits a fork on disk. The app it builds is the part-1 template.

**Core stance (systemPlan.md):** the **spec is the source of truth** (`system/`);
generated files are disposable build artifacts (tracked in `.compiler-manifest.json`,
freely clobbered). You change behavior by editing `system/` and rebuilding — never by
hand-editing generated output (`compile.sh check` flags that).

---

## 2. The three stages (the pipeline)

```
layer1.dream.md  --distill-->  layer2.params.yaml                 (LLM-creative)
                 --expand --> layer3.subparts/*.yaml               (LLM-creative)
                 --build  --> config roots + domain/ + fences + Help tree (deterministic; verified)
```

Creativity concentrates **early** (distill/expand draft editable YAML you correct);
precision + verification concentrate **late** (build is a pure data transform, then the
gate). The LLM is reserved for distill/expand and for free-form gate *code*; everything
else in `build` is deterministic.

---

## 3. The boundary that makes rebuilds safe (read before editing)

| Layer | Owned by | Regenerated? |
|---|---|---|
| generic engine — `core/` (minus the domain reads), `web/`, the Flask/SocketIO scaffold | the template | **No** — stays ≈ template so forks can pull upstream |
| the `domain/` pack — `gates.py` · `mock_rules.py` · `sequences.yaml` · `identity.py` | the Compiler | **Yes**, whole files |
| the config roots — `fleet/` · `profiles/` · `commands/` · `networks/` | the Compiler (the `live` items) | **Yes** |
| fenced label regions — `<!-- GEN:identity --> … <!-- /GEN -->` in templates | the Compiler | **Yes** (region only) |
| the Help tree — `design/` (served on the Help page) | the Compiler (`emit_docs`) | **Yes** — generated `00-about.md` + glossary; app-name **relabel only** (the reference docs are the shared engine, so structural keys `roleA`/`serviceA`/GATE A stay) |

**Labels are data; identifiers + brand tokens stay.** Operator-facing names come from
the spec; `ccflet` / `CCFlet` / `CCFLET_` / `/tmp/ccflet` / `X-CCFlet-User` are
load-bearing — never renamed (CLAUDE.md §8). The `domain/` pack and its `mock ↔ status`
string contract are described in **CLAUDE.md §4/§7** (that's the template-side view); the
Compiler's job is to re-emit both halves together.

---

## 4. `compiler/` module map

| File | Role | Nature |
|---|---|---|
| `cli.py` | argparse → subcommands (`new`/`scaffold`/`check`/`status`/run); `compile.sh` → `python -m compiler` | I/O |
| `pipeline.py` | the `--from/--to/--only` **range** orchestration + status transitions + the rules that protect edits | orchestration |
| `spec.py` | read/write the wish-list layers + the `build.yaml` **status book** (approved/draft/stale) | mostly pure |
| `stages.py` | **distill** (dream→params) + **expand** (params→sub-parts): LLM call **with an offline heuristic fallback** | mixed |
| `llm.py` | LLM backend: `claude` (headless `claude -p`) \| `offline` (deterministic, network-free) | I/O |
| `build.py` | the **deterministic emit**: params+sub-parts → identity/fleet/commands/networks/sequences + threshold patch + the **Help tree** (`emit_docs`) + compile report | **pure** transform |
| `gate.py` | the **acceptance gate**: `pytest` + `--mock` boot + `--dry-run`, driven over HTTP on an ephemeral port | I/O |
| `manifest.py` | `.compiler-manifest.json` — owned paths + content hashes; `check` = drift detection | pure |
| `catalog.py` | the overridable sub-part defaults (machine source for `scaffold`; mirrors `system/catalog.md`) | pure |
| `fork.py` | copy template → `apps/<name>/` (excludes `.venv`/`.git`/`runs`/`apps`/caches) | I/O |

**Pure vs I/O split (same discipline as the engine):** the logic that can be wrong —
the build transform, range selection, status transitions, manifest hashing — is pure and
unit-tested in `tests/test_compiler.py` with no network. `gate.py` (subprocess + HTTP)
and `llm.py` are the thin I/O shells, exercised by an actual fork build.

---

## 5. The `build.yaml` status model + the rules that protect your edits (R7)

`system/build.yaml` records each stage's artifact + a **status**: `approved` (you blessed
it) · `draft` (Compiler-made) · `stale` (an upstream artifact changed). `pipeline.run`:

- **Range-only** (`_select`): a run executes only the transforms whose input/output stages
  fall in `from..to` (or the single transform that produces `--only X`).
- **Approved lock**: a transform whose **output** stage is `approved` is skipped unless
  `--force` — which also makes an upstream re-run *halt* before it cascades over a
  downstream artifact you blessed.
- **Staleness** (`_mark_downstream`): running a transform marks later stages `stale`.
- **Stops at editable checkpoints**: `--only` / a `--to` short of `app` returns with a
  "review, then mark approved" reminder.

`STAGES = (dream, params, subparts, app)`; the three `_TRANSFORMS` map consecutive
stages. To add a stage, extend both + `STATUS_*` in `compiler/__init__.py`.

---

## 6. The acceptance gate (R6) — the correctness oracle

A `build` is "done" only when `gate.run_gate(app_dir, python)` is green, **inside the
built fork**:

1. `pytest -q` — the app's own suite (pure-logic + mock-backed) against its `domain/`.
2. `app.py --mock` — boots the sim fleet, brings it up, asserts a node reaches **GATE
   A+B = ok** via `/api/node/<n>/status` (a fresh synchronous poll). This is what proves
   the `mock_rules ↔ gates` string contract survived regeneration.
3. `app.py --dry-run` — a single `serviceA_start` action returns `[dry-run] …`.

A red gate ⇒ `pipeline.run` returns non-zero and the build is reported **NOT VERIFIED**
(it ships nothing trustworthy). The gate runs from the *template's* `compiler/` against
the *fork's* files, so fixing gate logic here applies to all builds immediately.

> **Known sharp edge:** the gate runs the *app's* `tests/`, some of which assert the
> **example** config (`test_commands` wants `df_data`/`archive_runs`; `test_networks`
> wants `link1/2/3`). The demo reproduces those, so it's green. A fork whose spec changes
> those config roots must also regenerate or relax those example-pinned tests — the
> Compiler does **not** yet emit per-app tests (backlog; see §8).

---

## 7. The offline backend (why the pipeline runs without a model)

`llm.resolve(pref)` maps `auto` → `claude` if the CLI is on PATH, else `offline`.
`stages.distill`/`expand` try the model, then fall back to a deterministic
template-default draft (R2): distill heuristically pulls an app name + node count from the
prose; expand emits default sub-parts. This keeps every checkpoint valid and the whole
pipeline **verifiable with no network** — the demo build pins `llm: offline`. The build
stage never calls the LLM (free-form gate logic is flagged as a TODO in the compile
report, never guessed — systemPlan §6).

---

## 8. Where to make common improvements

| You want to… | Touch | Then |
|---|---|---|
| add an overridable sub-part | `catalog.py` (`PARTS`) + an `emit_*` in `build.py` + a row in `system/catalog.md` | `tests/test_compiler.py` |
| change what `build` emits for an existing part | `build.py` (`emit_identity`/`emit_fleet`/`emit_commands`/…) | unit test + a real fork build |
| add a config root to the emit | `build.py` (a new `emit_*`, record it in the manifest) | fork build + `check` |
| change how the **Help tree** is regenerated | `build.py` (`emit_docs` / `_about_markdown`) + the `docs` part in `catalog.py` | `tests/test_compiler.py::test_emit_docs_*`; refresh on demand with `compile.sh … docs` |
| change a gate check at build time | a `gate-*` sub-part `thresholds:` → `build.patch_gate_thresholds` (numeric only); free-form logic is a TODO for LLM codegen into `domain/gates.py` | the gate |
| improve distill/expand drafts | `stages.py` (the heuristic and/or the `_*_PROMPT`) | `tests/test_compiler.py::test_offline_distill_*` |
| add an LLM provider | `llm.py` (`resolve` + `complete`) | offline still must work |
| tighten the gate | `gate.py` (add a check to `run_gate`) | a fork build |
| change range/҂status rules | `pipeline.py` (`_select`, `_mark_downstream`, approved-lock) + `compiler/__init__.py` (stages) | `tests/test_compiler.py::test_select_*` |
| **emit per-app tests** (close the §6 edge) | new `emit_tests` in `build.py` from the spec; mark example tests generated | the gate (its own tests) |

**Workflow for a compiler change:** edit → add/adjust a pure unit test in
`tests/test_compiler.py` → `pytest` → `./compile.sh new tmp && ./compile.sh --app
apps/tmp --from subparts --to app` and confirm the gate stays green → `rm -rf apps/tmp`.

---

## 9. Conventions & gotchas

- **`build` is a pure transform** — keep new emit logic deterministic and idempotent
  (re-running a build must produce identical bytes; `_pyrepr` exists to make
  `identity.py` stable). Anything non-deterministic belongs in distill/expand, not build.
- **The gate runs from the template, on the fork.** `compile.sh` runs the *template's*
  `.venv/bin/python -m compiler`; the fork has no `.venv`. So gate/build code edits take
  effect on the next run without re-forking; only the fork's `domain/`/config/tests are
  what's verified.
- **Forks live in `apps/`** (git-ignored). `fork.py` excludes `.venv`/`.git`/`runs`/
  `apps`/caches; a fork drops any inherited manifest so it owns its own outputs.
- **Don't break the offline path.** Every LLM call must degrade to a valid offline draft,
  or builds become un-runnable in CI / without a key.
- **Keep `catalog.py` and `system/catalog.md` in sync** (the machine + human views of the
  same menu), and `compiler/__init__.py::STAGES` consistent with `pipeline._TRANSFORMS`.

---

## 10. Backlog (Rebuild system)

- **Per-app test emission** — the gate runs example-pinned tests; generate/relax them per
  spec so a fork that changes config roots stays green (§6).
- **Richer deterministic build** — profiles/groups round-trip; more of config-root
  generation as a pure transform (systemPlan §12).
- **LLM gate codegen** — turn free-form `parse:`/`good:` prose into verified
  `domain/gates.py`, with the gate as the acceptance oracle and a bounded repair loop.
- **Richer per-app Help docs** — `emit_docs` today generates the `00-about.md` front
  page + glossary and relabels the display name; the engine reference docs stay shared
  (structural keys are literal there). Authoring/removing whole per-app doc *pages* from
  the spec (vs relabel) is the next step (likely LLM-assisted, gated like gate codegen).
- **`--check` in CI** + a small in-app **Build** page (systemPlan Phase 4).
