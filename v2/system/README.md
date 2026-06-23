# `system/` — the wish list (this app's spec)

This folder is **the only thing you hand-author** in a ccFleet app. The Compiler turns
it into a working, verified app. The app is a *build artifact*: change behaviour by
editing these files and rebuilding — never by hand-editing generated output (D3).

```
system/
  layer1.dream.md          YOU WRITE — free prose: what the app should do
  layer2.params.yaml       DRAFTED → you edit — global facts (names, nodes, gates)
  layer3.subparts/*.yaml   DRAFTED → you edit — one file per UI region, full knobs
  build.yaml               pipeline control + per-stage status (approved/draft/stale)
  SPEC.md                  the format + the Compiler's contract  ← read this
  catalog.md               the overridable sub-parts (+ `scaffold <part>`)
  compile-report.md        GENERATED — what was specified vs inferred + TODOs
```

This particular `system/` is the **template's own demo**: building it reproduces the
default ccFleet app, so a clean build still passes the acceptance gate. Fork the
template (`./compile.sh new <name>`) and rewrite `layer1.dream.md` for your own app.

Quick start:

```bash
./compile.sh new myapp                                  # fork → apps/myapp/
$EDITOR apps/myapp/system/layer1.dream.md               # write the dream
./compile.sh --app apps/myapp --only params             # distill → review layer2
./compile.sh --app apps/myapp --from params --to app    # expand + build + verify
```

See `SPEC.md` for everything; `../systemPlan.md` for the why.
