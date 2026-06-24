---
title: "Config & Commands"
order: 13
summary: "Config over code: edit fleet/profiles/variants/commands from the browser, validated + hot-reloaded + audited; plus operator-defined remote & local commands."

---

# Config & Commands (config over code)

## Overview

The person who *runs* `ccFleet` in the field **cannot edit, see, or run the
source.** So everything they may need to change at runtime is **operator-editable
config reachable from the browser**, not code:

- **`fleet/fleet.yaml`** — inventory, defaults, the A/B default variant, and the
  per-variant parameter sets.
- **`profiles/{roleA,roleB}.yaml`** — the per-role action/collector/log catalogs.
- **`commands/commands_{host,roleA,roleB}.yaml`** (+ `commands/*.sh`) — a catalog of
  *extra* triggerable commands, each a button. **The file decides where the command
  runs** — `commands_host.yaml` runs **locally** on the base station,
  `commands_roleA.yaml` / `commands_roleB.yaml` run on the **roleA** / **roleB** host
  over SSH — so the operator sees local vs remote at a glance and never has to spell
  out `on:`/`role:`.
- **`networks/networks.yaml`** — the off-fleet links for the top-bar LEDs
  ([14](14-connectivity-leds.md)).

> **All these roots live inside the active *config profile*, `yamls/<profile>/` (default
> `yamls/default/`).** A config profile is one whole self-contained set of the roots above;
> the app reads from exactly one at a time, and the operator can flip the live one — hot,
> isolated — from the header pill (see **Config profiles** below). Paths here are written
> profile-relative.

All of these are served read-write by the **Config** page (`/config`) — the
read-write twin of the read-only **Help** page. A save is **validated before it
takes effect, backed up, hot-reloaded with no restart, and audited** like any other
action (P6).

## Data flow

```
browser (Config page)
   │  GET /api/config/tree        ── list editable roots/files (read fresh)
   │  GET /api/config/file        ── raw text + kind + mtime
   │  POST /api/config/validate   ── check only (the "Check" button)
   │  POST /api/config/file       ── save: validate → backup → write → reload → audit
   │  POST /api/config/revert     ── restore newest backup
   │  POST /api/config/profile    ── switch the active config profile (hot, all scopes)
   ▼
core/config_store.py  ── path-safe roots, extension allow-list, per-kind validators
   ▼
CCFletApp.reload_config(scope)  ── hot-reload in place, broadcast, audit
   ├─ fleet     → Fleet.reload_from_dict (in place; variant/algo preserved if valid)
   ├─ profiles  → ProfileManager.invalidate + Orchestrator.reload_profiles
   ├─ commands  → CommandCatalog.reload
   ├─ networks  → Networks.reload_from_dict (in place; NetMonitor re-polls the LEDs)
   └─ (mock)    → MockFleetState.reload ;  always → ConnectionPool.close_all
```

Custom commands are rendered as buttons **client-side** from `GET /api/commands`, so
editing a catalog file and reloading changes the UI with **no template edit**.

## Config profiles (switch the whole editable-YAML set)

A **config profile** is a complete, self-contained copy of *every* root above. Every
profile — `default` included — is a subdir of one aggregation root, with the same
config-root subdirs:

```
yamls/
  active                 # the persisted active profile name (a local runtime choice)
  default/               # the baseline profile
    fleet/  profiles/  commands/  networks/  gates/  logs/
  <name>/                # an operator-made alternate (a sandbox, a staging fleet, …)
    … same subdirs
```

The app reads its live config from **exactly one active profile** at a time, so an operator
can keep a real `default` fleet and a throwaway sandbox side by side and **flip between them
from the header pill** — hot, no restart, and **with no effect on the other profile's
files**. Picked at boot by `--profile <name>` (else the persisted choice, else `default`);
a per-root flag such as `--fleet` still overrides just that one root of the active profile.

- `GET /api/config/tree` returns `{active, profiles}` alongside the roots; `GET
  /api/config/profiles` lists them.
- `POST /api/config/profile {name}` — switch the live set: repoint every root **in place**
  and reuse the same per-scope `reload_config` a save uses (fleet · profiles · commands ·
  states · gates · logs), then persist + audit (`CONFIG_RELOADED`) + broadcast
  (`profile_changed`).
- `POST /api/config/profile/new {name, from}` — scaffold a clone (copies only the editable
  `*.yaml`/`*.sh`, never backups); does **not** switch.

Engine: `core/config_profiles.py` (pure path/scaffold logic) + `CCFletApp.switch_profile`.

## The command catalog (`commands/commands_{host,roleA,roleB}.yaml`)

Parsed and validated by `core/commands.py` (pure, unit-tested), modeled on
`profiles.py`. **The catalog is split by *where a command runs*** — one file per
target — so what runs locally vs remotely is obvious from the filename, and the
common `on:`/`role:` fields are implied by the file (no per-entry boilerplate, and no
YAML `on:`-boolean footgun). `CommandCatalog` reads every present file in `commands/`
and merges them; a name appearing in two files is rejected (loud, atomic — the prior
catalog is kept).

| File | Implied defaults | Runs on |
|---|---|---|
| `commands_host.yaml` | `on: local`, `scope: fleet` | 🖥 the base station (subprocess) |
| `commands_roleA.yaml` | `on: remote`, `role: roleA` | 🛰 the node's roleA host over SSH |
| `commands_roleB.yaml` | `on: remote`, `role: roleB` | 🛰 the node's roleB host (via the roleA jump-host; variant B) |
| `commands.yaml` | *(none)* | legacy single file — still loaded for back-compat |

```yaml
# commands_roleA.yaml — every entry here is on: remote, role: roleA (the file says so)
settings:
  default_timeout: 60
commands:
  df_data:                       # diagnostics, runs on the node over SSH
    label: "Disk /data"
    group: Diagnostics
    scope: node                  # node → shows on the node page
    run: "df -h {DEPLOY_ROOT}"   # inline command  (xor `script:`)
  reboot_roleA:
    label: "Reboot roleA"
    group: Maintenance
    scope: node
    run: "sudo reboot"
    danger: true                 # red styling + audit emphasis (NO confirm — P6)
    timeout: 20

# commands_host.yaml — every entry here is on: local (the file says so)
commands:
  archive_runs:                  # housekeeping, runs on the base station
    label: "Archive old runs"
    group: Housekeeping
    script: "archive_runs.sh"    # a file under commands/
    timeout: 120
```

| Field | Meaning |
|---|---|
| `label` / `group` | button text / section header |
| `on` | `remote`/`local` — **implied by the file**; rarely set per-entry |
| `role` | `roleA` \| `roleB` — **implied by the file** (remote only) |
| `scope` | `node` (shows per-node, fans out across a selection for remote) or `fleet` |
| `session_scope` | for `fleet` buttons: `fullPage` \| `downPage` \| `both` (default) — which session surface shows the button (full session page vs the bottom dock) |
| `run` | inline command — **xor** `script` |
| `script` | a file under `commands/` (resolved path-safely) |
| `timeout` | seconds; defaults to `settings.default_timeout` |
| `danger` | visual + audit emphasis only — **no confirmation prompt** (P6) |

Each file's implied fields are *defaults*: an entry may still set `on`/`role`/`scope`
explicitly to override. `config_store` validates a file with the same per-file
defaults (`commands.file_defaults`), so the Config-page **Check** judges a file
exactly as it loads.

> **`commands/*.sh` ≠ `scripts/*.sh`.** A `script:` named here is part of the
> *catalog* — invoked by a dashboard button, `{param}`-rendered, hot-reloaded and
> audited (P6/P8). The repo also has a top-level **`scripts/`** dir of *standalone*
> base-station helpers (e.g. `clear_sessions.sh`) that you run from a terminal;
> `ccFleet` never invokes those and they aren't validated/audited. See
> `scripts/README.md` and [09 — Sessions & Audit](09-sessions-and-audit.md).

`{param}` substitution: **remote** commands get the node's `fleet.params(node)` dict
(the same `{ID}`/`{HOST_A}`/`{DEPLOY_ROOT}`… bare-token-safe values the profiles
use); **local** commands get a base-station context (`fleet`, `variant`, `algo`,
`runs_dir`, selected nodes) substituted into the command and passed as `CCFLET_*` env
to scripts.

Execution lives in `Orchestrator.run_custom(name, node=None, nodes=None, user=None)`:
remote runs via the pooled SSH client (`scope: fleet` fans out across the selection);
local runs via `core/local_exec.run_local(...)`. Both produce the same
`CommandResult`/`ActionResult` shape, so they are audited identically (P6).

## Key decisions

- **Validate before apply, never persist invalid.** `config_store.validate` parses
  the *submitted text* into the real model (`fleet.fleet_from_dict`,
  `profiles.profile_from_dict`, `commands.commands_from_dict`,
  `networks.networks_from_dict`) and only writes if it succeeds. A non-coder gets a
  line-numbered error instead of a broken fleet.
- **Backup + revert.** Every write first copies the prior file to `<root>/.bak/`; the
  Config page's **Revert** restores the newest. **Revert re-validates the backup
  before restoring it** (a backup can predate a schema change), so it can never write
  an invalid file either; the `/api/config/validate` "Check" mirrors the write path
  (same root/extension/traversal resolution) so it never green-lights a save that
  would be refused. The audit log + backups are the safety net (consistent with P6).
- **Hot-reload in place.** The `Fleet` (and `Networks`) instance is mutated in place
  because the orchestrator, the client factory closure, the mock state and the LED
  monitor all hold the *same* reference; the current runtime `variant`/`algo` survive
  a reload if still valid. The connection pool is closed so changed hosts reconnect
  lazily.
- **Buttons from config, not templates.** Custom-command buttons are built from
  `/api/commands`, so the catalog is the single source of truth for the UI.
- **Remote vs local is always visible.** Each button carries a remote (🛰) or local
  (🖥) chip and a distinct border, so the operator can see *where* a command runs.

## Constraints / Invariants

- **Path safety.** Every Config read/write goes through `config_store` — registered
  roots only, extension allow-list (`.yaml`/`.yml` for fleet/profiles/commands/networks,
  `.sh` for scripts), no traversal (same `safe_resolve` discipline as `docs.py`).
  Never join a request path onto a config root by hand.
- **Config-as-code is intentional (P8), fenced by posture.** Authoring YAML/scripts
  and triggering arbitrary shell from the browser is the feature — acceptable only
  because the app is LAN-only, no-auth, bound to a chosen interface, and audits
  everything (P6). Keep node-derived `{param}` values bare tokens; take **no
  free-form runtime args** into a command.
- **Local exec is the higher-blast-radius path.** It is **echo-only under
  `--mock`/`--dry-run`**, gated by `--no-local-commands`, and runs as the app user.
- **Audit everything (P6).** Saves emit `CONFIG_SAVED` + `CONFIG_RELOADED`; custom
  commands emit the usual `ACTION_STARTED`/`COMPLETED`/`FAILED` with
  `extra={on, scope, danger}`. The completion carries every per-target `result`
  (`stdout`/`stderr`/`exit_code`), so the captured output is durably in `events.jsonl`.
- **The session timeline stays compact.** A custom-command completion renders as a
  one-line `… — N/N ok`; the captured output is **folded behind a `▸ output` toggle**
  (`web/templates/session_view.html`) that expands a per-target block inline — output is
  recorded and reviewable without overloading the timeline.

## Change points

- **Add a triggerable command** → edit the right file from the Config page —
  `commands_host.yaml` (local), `commands_roleA.yaml` / `commands_roleB.yaml`
  (remote) — and drop a `commands/*.sh` if you need a script. No code.
- **Add a command target / change its implied defaults** → `commands.py::CATALOG_FILES`.
- **Add an editable config root or a validator** → `config_store.py` (`ROOTS`,
  `validate`). `tests/test_config_store.py`.
- **Add a command field / change rendering** → `commands.py` schema +
  `web/templates/{node,dashboard}.html` button builder.
- **Change what a reload touches** → `CCFletApp.reload_config` + the per-module
  `reload_*` hooks (`Fleet.reload_from_dict`, `ProfileManager.invalidate`,
  `Orchestrator.reload_profiles`, `Networks.reload_from_dict`, `MockFleetState.reload`).

## Open questions

- **Optimistic-concurrency on saves.** Two operators editing the same file: last
  write wins today (backups soften it). An mtime/ETag check could reject a stale
  save — deferred.
- **Long-running custom commands** return captured output; streaming a long command
  into the xterm pane (like log tails) is a possible follow-up.
- **Per-command visibility / ordering** beyond `group` (e.g. pinning, hiding) is not
  modeled yet.
