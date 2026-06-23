# The Rebuild system — wish list → working app (part 2 of 2)

> **This repo has two parts** (see the short root [`README.md`](README.md)). This is the
> doc index for **part 2 — the Rebuild system (the Compiler)**: it forks the ccFleet
> template and **builds a complete, verified app from a short `system/` wish list**, then
> **rebuilds** it whenever the wish list changes. Part 1, the template app itself, is
> documented in [`README_template.md`](README_template.md).

## The idea

Adapting the template by hand is slow and drifts. Instead you **describe** an app in a few
small, human-friendly files (a wish list), **build** it into a working app with one
command, and **rebuild** it predictably when the description changes — never hand-editing
generated output. The spec is the source of truth; the app is a build artifact.

```
layer1.dream.md   free prose: what the app should do        (you write — a paragraph)
   │ distill
layer2.params.yaml  global facts (names, nodes, gates)       (drafted → you edit)
   │ expand
layer3.subparts/*.yaml  one file per UI region, full knobs   (drafted → you edit)
   │ build
APP               config roots + domain/ + fenced labels      (VERIFIED by the gate)
```

A build is "done" only when the **acceptance gate** is green: `pytest` + `app.py --mock`
(the fleet lights up) + `app.py --dry-run`. A red gate ships nothing.

## Quick start

```bash
./compile.sh new myapp                                   # fork template → apps/myapp/
$EDITOR apps/myapp/system/layer1.dream.md                # write the dream
./compile.sh --app apps/myapp --only params              # distill → review layer2.params.yaml
./compile.sh --app apps/myapp --from params --to app     # expand + build + verify
# … or in one shot:
./compile.sh --app apps/myapp --from dream --to app
```

Other commands: `scaffold <part>` (dump a sub-part default to edit), `check` (warn if a
human hand-edited generated output), `status` (per-stage state). Forks live in `apps/`
(git-ignored).

## Docs

- [`system/SPEC.md`](system/SPEC.md) — **the wish-list format + the Compiler's contract**
  (the three layers, the `mode:` live/frozen flag, the build rules). Start here.
- [`system/catalog.md`](system/catalog.md) — every overridable sub-part + `scaffold <part>`.
- [`system/README.md`](system/README.md) — the `system/` folder, layer by layer.
- [`CLAUDE_rebuild.md`](CLAUDE_rebuild.md) — the **engineering brief**: the `compiler/`
  module map, the `build.yaml` status model, the acceptance gate, and where to change the
  build system.
- [`systemPlan.md`](systemPlan.md) — the design rationale (requirements R1–R8, the locked
  decisions, the roadmap) behind all of the above.

## How it stays safe to rebuild

- **Editable checkpoints** — every stage output is a file you read and correct before it
  flows downstream.
- **`mode:`** decides per item: `live` (lands in the Config roots, operator-editable at
  runtime) vs `frozen` (baked into `domain/` code, change the spec + rebuild).
- **Range-safe** — `--from/--to/--only` touch only the requested transforms; `approved`
  stages are locked; editing an artifact marks downstream `stale`.
- **Owned-paths manifest** — `.compiler-manifest.json` lets a rebuild clobber exactly the
  generated files; `compile.sh check` flags any hand-edit (the spec is the source of truth).

## The template *is* its own demo

This repo's own [`system/`](system) describes the default ccFleet app, so building it
reproduces part 1 — and the acceptance gate proves the Compiler stayed faithful. It's the
worked example to copy from.
