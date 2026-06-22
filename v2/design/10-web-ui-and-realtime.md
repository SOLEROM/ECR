---
noteId: "90f5fc706b0311f1b060577f73b9a94a"
tags: []
title: "Web UI & Realtime"
order: 10
summary: "The REST pages, the SocketIO room model, multi-operator presence, and how live state reaches the browser."

---

# Web UI & Realtime

## Overview

The web layer is a **thin adapter**: pages and a small REST surface in
`web/routes.py`, a shared client in `web/static/js/app.js`, and a real-time bus in
`core/sync.py`. It holds no control logic — it validates input, calls the `CCFletApp`
facade, and renders state. Two operators can watch and drive the same fleet at
once; everything they do shows up live for everyone via SocketIO rooms.

## Pages & REST surface (`web/routes.py`)

| Page | Shows |
|---|---|
| Dashboard | the fleet grid, gate roll-up, algo selector, fleet actions |
| Node | one node's detail, per-daemon actions, the A/B variant toggle, live log panes |
| Sessions | list of ops sessions, open/close/export |
| Session view | one session's event timeline + captured logs |
| Config | the read-write editor for fleet/profiles/commands/networks YAML + scripts |
| Help | the read-only render of this `design/` tree |

REST endpoints back these: fleet info + algo, per-node variant + actions, session
create/list/get/delete/export, and the Config/commands/networks APIs. Background
work (sequences, fan-out) is dispatched via `CCFletApp.run_bg` so HTTP returns
immediately and progress arrives over the socket. `set_algo` is validated
server-side and returns 400 on bad input.

## The realtime bus (`core/sync.py`)

State changes are pushed, not polled. `SyncManager` manages SocketIO and the rooms:

- **`FLEET_ROOM`** — every connected operator; receives fleet-wide events:
  `new_event`, `node_status`, `gate_changed`, `node_variant` (per-node A/B toggle),
  `net_status` (the top-bar LEDs), `roster`.
- **`stream_room(node, log)`** — subscribers to one live log pane; receives tailed
  lines ([08](08-connectivity-and-streaming.md)).

Emit helpers wrap each message type so the engine never touches SocketIO directly.
`on_disconnect` updates presence.

## Multi-operator presence

Each client sends an identity header `X-CCFlet-User` (set in `app.js`); the server
tracks a **roster** of who's connected and broadcasts it on join/leave. Because the
audit log records the actor on every event ([09](09-sessions-and-audit.md)), the
timeline shows *who* did *what* — the social safety layer that replaces locks/RBAC
on a trusted LAN.

## Client (`web/static/js/app.js` + templates)

A small shared `CCFlet` object: the socket connection, the identity, an `api()`
helper that attaches `X-CCFlet-User`, toast notifications, and the roster widget.
Each node card/detail seeds its own A/B variant toggle from `/api/fleet` (per-node)
and updates it on `node_variant`. Templates are server-rendered
(`base/dashboard/node/sessions/session_view/config/help`); live updates patch the
DOM from socket events. Log panes use vendored **xterm.js**.

## Key decisions

- **Thin web layer.** No SSH, sequencing, or parsing in routes/templates — they
  call the facade. Keeps the testable logic in `core/`.
- **Push over poll.** SocketIO rooms broadcast state transitions; the browser
  doesn't hammer REST for status.
- **Rooms scope traffic.** Fleet-wide vs per-log rooms mean a busy log pane doesn't
  spam operators who didn't open it.
- **Presence + audit instead of RBAC.** On a closed LAN, showing who's connected and
  attributing every action is the chosen coordination model (see
  [02](02-scope-and-decisions.md), [12](12-security-and-operations.md)).

## Constraints / Invariants

- Untrusted strings (operator notes, usernames) render via **`textContent`, never
  `innerHTML`** — XSS guard. Keep it that way.
- The web layer stays an adapter; push new behavior into the facade/engine.
- The engine emits through `sync.py` helpers, not raw `socketio.emit`, so the room
  model stays in one place.
- Wire events use the `type` field ([09](09-sessions-and-audit.md)); the client
  reads `type`.

## Change points

- **Add a page / endpoint** → `web/routes.py` + a template + `app.js`; smoke with
  `--mock`.
- **Add a broadcast message** → an emit helper in `sync.py` + a client handler.
- **Restyle the UI** → `web/static/css/main.css` (dark theme).
- **Change identity handling** → `X-CCFlet-User` plumbing in `app.js` + `routes.py`.

## Open questions

- Identity is self-asserted (`X-CCFlet-User`), not authenticated — fine under the
  no-auth posture, but it means presence/attribution are honor-system.
- No optimistic UI/conflict resolution if two operators act on one node within the
  same instant; the per-node lock serializes the *engine*, but the UIs may briefly
  disagree until the next broadcast.
