---
noteId: "ad4721b06b0311f1b060577f73b9a94a"
tags: []
title: "Security & Operations"
order: 12
summary: "The no-auth posture and the guardrails that make it safe, plus how to run, dry-run, and deploy ccFleet."

---

# Security & Operations

## Overview

`ccFleet` runs on a **closed LAN** for **trusted operators**, so it has **no auth or
RBAC by decision** ([02](02-scope-and-decisions.md)). That posture is only
acceptable because of two things: the deployment is locked down (bound interface,
no internet path), and the code is hardened against the injection classes that
*would* matter even among trusted users (a fat-fingered algo name shouldn't be able
to run arbitrary shell). This doc covers those guardrails and the day-to-day run
loop.

## The no-auth posture (and why it's safe enough)

- Closed LAN, trusted operators, bind to a chosen interface (`--host`).
- **Audit everything** ([09](09-sessions-and-audit.md)) — every action+result is
  attributed and logged; the record, not a permission check, is the accountability.
- **Presence** ([10](10-web-ui-and-realtime.md)) — operators see who else is
  connected, so coordination is social rather than enforced.

Revisit only if asked; a read-only viewer or basic auth would be the first step.

## Injection guardrails (these matter regardless of auth)

Any value that reaches a shell or the filesystem is validated or quoted at the
boundary:

| Surface | Risk | Guard |
|---|---|---|
| `algo` (→ remote command) | shell injection | `ALGO_RE = ^[A-Za-z0-9_-]+$` in `Fleet.set_algo`; bad input → `ValueError` → HTTP 400 |
| `session_id` (→ filesystem) | path traversal | `SessionManager._safe_dir` rejects `..` / abs / separators |
| operator notes, usernames (→ DOM) | XSS | rendered via `textContent`, never `innerHTML` |
| node / fleet `name`, `host`, `subnet` (→ command) | injection | validated at load — `NAME_RE` for names, `HOST_RE` for hosts (bare tokens); action names come from server-side config |
| synthesized command args | injection | single-quoted in `supervisor.py` (`_sq`) |
| Config file path (→ filesystem) | traversal / arbitrary write | `config_store._resolve` — registered roots, extension allow-list, no `..`/dotfiles; writes validate-then-atomic |
| command `{param}` (→ remote shell) | injection | node params are bare tokens (`fleet.params`); script env via `shlex.quote` (`_env_prefix`); **no free-form runtime args** |
| connectivity-LED `host` (→ ping argv) | injection | config-validated bare token, passed as an argv element (no shell) — see [14](14-connectivity-leds.md) |
| local (base-station) command | RCE blast radius | echo-only in `--mock`/`--dry-run`; gated by `--no-local-commands`; runs as the app user |

**Rule for any new feature:** if a user-supplied value reaches a shell, validate it
or `shlex.quote` it; if it reaches the filesystem, route it through `_safe_dir` /
`config_store`; if it reaches the DOM, use `textContent`.

## Config over code (P8) — a deliberately wider trust surface

The **Config** page ([13](13-config-and-commands.md)) lets a trusted operator edit
YAML/scripts and define commands that run **arbitrary shell** — on a node (remote) or
on the base station (local). That is the *feature* (the operator can't change code),
not a hole; it is acceptable for the **same reason** the app has no auth: a closed
LAN, a bound interface, trusted operators, and a full audit trail. It does raise the
stakes, so:

- Treat **network access to ccFleet as equivalent to a shell** on the base station
  and on every node. Bind to a trusted interface (`--host`), never expose it to an
  untrusted network. `--public` is a shortcut that binds `0.0.0.0` so the whole LAN
  can reach it — convenient, but it puts the **no-auth, shell-equivalent** app on
  every host that can route to the station, so use it only on a closed LAN. If the
  network can't be trusted, auth (reversing the §02 posture) comes *before* anything
  else.
- The guards above still hold: the editor can only touch the **registered roots**
  with **allowed extensions** (no traversal), saves **validate before they apply**,
  and the only "untrusted" text is the operator's own authored config — requests
  carry **no free-form arguments** into a command.
- **Local** commands are the higher-blast-radius path: echo-only under
  `--mock`/`--dry-run`, and disable-able with `--no-local-commands` for deployments
  that only want remote ops.
- Every save (`CONFIG_SAVED`/`CONFIG_RELOADED`) and every command (`ACTION_*` with
  `on`/`scope`/`danger`) is audited (P6) — the record is the safety net.

## Running ccFleet

Deps are **not** in the system Python (PEP 668). Use the venv at **`.venv/`** in the
project root (or just `./run.sh --mock`, which bootstraps it on first run):

```bash
# from the project root
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # one-time
.venv/bin/python app.py --mock          # simulated fleet → http://127.0.0.1:5000
.venv/bin/python app.py                 # real fleet (edit fleet/fleet.yaml)
.venv/bin/python app.py --dry-run       # print real SSH commands, run nothing
.venv/bin/python -m pytest              # ~181 tests, no network
```

**CLI flags:** `--host --public --port --fleet <yaml> --profiles-dir --commands-dir
--states-dir <dir> --runs-dir --mock --dry-run --no-poll --no-local-commands --debug`.
Default bind is `127.0.0.1:5000`; `--public` binds `0.0.0.0` (reachable across the
LAN — see the posture note above).

## Going to real hardware

- Edit `fleet/fleet.yaml` for the real nodes; ensure one SSH key works to every
  roleA host (and through it to each roleB host). One auth source feeds paramiko
  *and* rsync/scp.
- Nodes are assumed already provisioned + booted (principle **P7**).
- **Exercise one node first**, then fan out — the live node end-to-end path is the
  main surface not covered by tests/mock ([11](11-mock-and-testing.md)).
- `--dry-run` first to confirm the synthesized commands look right for your fleet.

## Operational notes / gotchas

- **SocketIO is `threading`, not gevent** — don't switch without re-validating
  paramiko threads ([08](08-connectivity-and-streaming.md)).
- **Launching the dev server from an automated shell:** use `setsid … &` and poll
  with `curl --retry`; a compound command that exits non-zero tears down the
  backgrounded server's process group (a harness quirk, not a bug).
- On shutdown, `ccFleet` stops its streams and closes the connection pool.

## Key decisions

- **No auth, but hardened inputs.** The trust boundary is the LAN; within it, the
  guardrails stop accidental (or careless) injection, which is the realistic threat.
- **One SSH auth source** in `fleet.yaml` for every transport.
- **Bind explicitly** (`--host`) rather than assuming localhost-only safety.

## Constraints / Invariants

- Never interpolate an unvalidated value into a shell or a path.
- Keep `textContent` for untrusted strings.
- Don't add secrets to the repo; SSH keys live on the operator's machine
  (`key_file` points at them), never in the tree.

## Change points

- **Add basic auth / a viewer role** → a Flask before-request guard + `routes.py`
  (reverses the no-auth posture — flag as a scope change, see [02](02-scope-and-decisions.md)).
- **Restrict the bind / port** → `--host` / `--port` (or a systemd unit for ccFleet).
- **Add a new validated input** → mirror the `ALGO_RE` / `_safe_dir` pattern at the
  boundary.

## Open questions

- No TLS on the web/socket traffic (plain LAN assumption); a reverse proxy with TLS
  is the path if that changes.
- No rate limiting on endpoints (single-tenant LAN); revisit if exposure widens.
- **SocketIO `cors_allowed_origins="*"`** (`app.py`) lets *any* browser origin open a
  WebSocket and read the live event stream (audit events, command output) — a
  read-only cross-origin leak (cross-origin REST *writes* are still blocked: the JSON
  POSTs are preflighted and the app sets no CORS header on them). It does **not**
  enable command execution. With P8 the stream now also carries config edits, so
  tightening this to the bound origin is a recommended hardening if the base
  station's browser is ever used on other networks. Left as a deliberate, documented
  default for now (closed LAN); flip it when you confirm the operator's access origin.
- Key distribution/rotation for the fleet is out of scope here (provisioning, P7).
