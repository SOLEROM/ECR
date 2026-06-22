---
noteId: "82e3d8006b0311f1b060577f73b9a94a"
tags: []
title: "Sessions & Audit Trail"
order: 9
summary: "The per-run ops session, the append-only event log that is the safety net, and ZIP export."

---

# Sessions & Audit Trail

## Overview

`ccFleet`'s only safety mechanism is **recording everything** (principle **P6**):
there are no confirmation prompts, so the audit trail *is* the seatbelt. A
**session** is one ops run — typically one operating run — captured as a
self-contained directory under `runs/`. Every action and every result is appended to
an immutable event log, alongside a snapshot of the fleet config and the live logs,
and the whole thing is exportable as a ZIP for post-run analysis.

## Anatomy of a session (`runs/<session_id>/`)

```
runs/<session_id>/
  manifest.json        # id, name, status, created_at, variant, algo, node_names
  events.jsonl         # append-only audit log (one JSON object per line)
  fleet_snapshot.yaml  # the exact inventory used for this run
  logs/                # captured per-node log streams
  artifacts/           # exported / collected files
```

`core/storage.py` owns this: `SessionManager` creates/lists/gets/deletes sessions
and generates ids; `SessionStorage` appends events and logs and builds the ZIP;
`SessionManifest` is the header record.

## The event log (`core/events.py`)

The log is **append-only JSONL** — one `Event` per line, never rewritten. An event
carries a type, timestamp, the actor, the node/action, and the result. Events are
the source for both the live UI feed and the post-hoc record.

**Two serialization shapes, on purpose:**

| Shape | Used by | Key field |
|---|---|---|
| `to_dict()` | the wire / UI (SocketIO, `new_event`) | `type` |
| `to_json()` / `_disk_dict()` | the on-disk `events.jsonl` | `event_type` |

This split is deliberate (it was a bug when they were conflated): the dashboard JS
reads `type`; the disk format uses `event_type`. `from_json` round-trips the disk
shape. Keep them distinct.

## Session lifecycle

A session is **opened** (status `open`), accumulates events/logs as the operator
works, and is **closed** when the run is done; it can then be exported. The active
session is owned by the `CCFletApp` facade so every action lands in the right place.
`fleet_snapshot.yaml` is written at creation so the record reflects the config
as-run, even if `fleet.yaml` changes later.

## Path safety

`session_id` is a filesystem path component, so `SessionManager._safe_dir` rejects
traversal (`..`, absolute paths, separators) before any create/get/delete touches
the disk. Every session-id path op routes through it. See
[12 — Security](12-security-and-operations.md).

## Resetting sessions (housekeeping)

There is **no delete-all in the UI** (the per-row delete and per-row ZIP export are
the only in-app session ops) and no automatic retention. To clear the whole list —
every session dir *and* every `*.zip` export under `runs/` — run the base-station
helper from a terminal:

```bash
scripts/clear_sessions.sh -n     # dry-run: list what would be deleted
scripts/clear_sessions.sh        # confirm, then delete all   (-y skips the prompt)
```

It identifies session dirs by their `manifest.json` (so it won't touch non-session
files), resolves the runs dir as `arg → $CCFLET_RUNS_DIR → ../runs`, and is
**destructive and irreversible** — run it with `ccFleet` **stopped** so you don't
pull a live session dir out from under the server, and export anything worth keeping
first. This is a standalone CLI tool under `scripts/` (see `scripts/README.md`),
**not** the operator command catalog (`commands/`,
[13 — Config & Commands](13-config-and-commands.md)): nothing in `ccFleet` invokes
it and it is not validated/audited.

## Key decisions

- **Audit-only safety (P6).** No dialogs, no gating — instead, *every* action and
  result is logged. Fast to operate, fully reconstructable after the fact.
- **Append-only JSONL.** Trivial to write incrementally, stream, and grep; an event
  is never mutated, so the record can't be quietly altered.
- **Self-contained session dirs + config snapshot.** A session ZIP is a complete,
  portable record of one run — what was run, against what fleet, with what result.
- **Wire vs disk event shapes kept separate** to serve the UI and the archive
  without coupling them.

## Constraints / Invariants

- The event log is append-only; never rewrite or reorder it.
- Every mutating action must produce an event (request) and an event (result) —
  that is the safety contract.
- All session-id filesystem access goes through `_safe_dir`.
- `fleet_snapshot.yaml` is captured at session creation, not export.

## Change points

- **Add a field to the audit record** → `Event` in `events.py` (update *both*
  `to_dict` and `_disk_dict`), then any reader.
- **Add a new event type** → `EventType` enum in `events.py`.
- **Change session layout / ZIP contents** → `SessionStorage` in `storage.py`
  (+ `tests/test_storage.py`).
- **Change the id scheme** → `generate_session_id` (keep it `_safe_dir`-clean).

## Open questions

- No *automatic* retention/pruning on `runs/`; it grows until cleared. A manual
  reset exists (`scripts/clear_sessions.sh`, above), but there is no age/size cap or
  scheduled prune.
- The audit log is local to the `ccFleet` host; shipping it to a central store
  (for fleets run from multiple base stations) is unscoped.
