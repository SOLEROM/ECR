# catalog.md — the overridable sub-parts

Every region of the app you can override from `layer3.subparts/`. **Skip a part → you
inherit the template default** (R2). Dump any part's current default as a ready-to-edit
file with:

```bash
./compile.sh --app apps/<name> scaffold <part>
```

The machine source for these defaults is `compiler/catalog.py` (kept in sync with this
table). The `extends:` id is what you put at the top of the Layer-3 file.

| part (`scaffold <part>`) | `extends:` | mode | emits | what it controls |
|---|---|---|---|---|
| `identity`      | `identity`      | live   | `domain/identity.py` (+ fenced template regions) | app name, brand wordmark, gate/role/service labels |
| `host-actions`  | `commands.host` | live   | `commands/commands_host.yaml`  | LOCAL 🖥 base-station command buttons |
| `roleA-actions` | `commands.roleA`| live   | `commands/commands_roleA.yaml` | REMOTE 🛰 roleA command buttons |
| `roleB-actions` | `commands.roleB`| live   | `commands/commands_roleB.yaml` | REMOTE 🛰 roleB command buttons |
| `networks`      | `networks`      | live   | `networks/networks.yaml`       | top-bar connectivity LEDs (off-fleet links) |
| `sequences`     | `sequences`     | frozen | `domain/sequences.yaml`        | deploy / bring-up / tear-down order + invariants |
| `gate-c`        | `gate.C`        | frozen | `domain/gates.py` (thresholds) | GATE C — the variant-B sensor/value check |
| `gate-d`        | `gate.D`        | frozen | `domain/gates.py` (thresholds) | GATE D — link / peer liveness |
| `docs`          | `docs`          | live   | `design/` tree (Help page)     | the app's generated front page + glossary, app-name relabeling |

## Docs sub-part shape (the Help tree)

The Help (`design/`) tree is the **shared engine reference**, so the build does **not**
rename structural identifiers in it (`roleA` / `serviceA` / GATE A are literal code
references there). Instead it generates an app-specific **front page + glossary**
(`design/00-about.md`) that maps each engine key onto your labels, and relabels only the
unambiguous **display name** across the tree. This part runs by default (no file needed);
override it only to tune that behavior:

```yaml
extends: docs
generate_about: true       # write design/00-about.md (app intro + key→label glossary)
relabel_app_name: true     # swap the display app name across the tree (brand tokens stay)
substitutions: {}          # extra literal from→to display-token pairs (advanced; verbatim)
exclude: []                # design/ relpaths to leave exactly as the template
```

Regenerate just the Help tree on demand (after editing the spec labels or the source
docs) without a full rebuild + gate: `./compile.sh --app apps/<name> docs`.

## Command item shape (action sub-parts)

```yaml
extends: commands.roleA          # the file decides where it runs (host=local, roleA/roleB=remote)
add:
  - id: uptime                   # → the command key (a bare token)
    label: "Uptime"
    group: Diagnostics           # button grouping
    scope: node                  # node | fleet
    run: "uptime"                # inline command  — XOR —
    # script: my_task.sh         # a *.sh file under commands/ (copied with the fork)
    timeout: 30
    danger: false                # red styling + audit emphasis (NO confirm — audit is the net)
    mode: live
remove: [old_button]             # drop a default item by id
```

`{param}` placeholders in `run:` are the node's derived params (`{ID} {HOST_A} {HOST_B}
{SUBNET} {VAR_ADDR} {ALGO} {VARIANT} {DEPLOY_ROOT}` …) — kept bare-token-safe.

## Gate sub-part shape

```yaml
extends: gate.C
label: humidity                  # the operator-facing gate label
applies_to_variant: B            # (informational) which variant the check applies to
thresholds:                      # numeric constants patched into domain/gates.py
  CHECK_GOOD: 3
  CHECK_FRESH_S: 1.0
parse: "..."                     # free-form parsing logic → flagged TODO (LLM codegen)
good:  "..."                     # free-form "is it healthy" logic → flagged TODO
mode: frozen
```

Only `thresholds:` are patched deterministically. Non-numeric `parse:`/`good:` logic is
recorded in the compile report as a TODO for review (never silently guessed, §6).
