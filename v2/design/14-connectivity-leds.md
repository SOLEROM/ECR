---
title: "Status LEDs (the States bar)"
order: 14
summary: "The States bar under the header shows one LED per operator-defined state — base-station ping links (networks.yaml) and command-driven states (stateA.yaml) whose exit code maps to a color."

---

# Status LEDs — the States bar

## What it is

Directly under the header is a full-width **States bar** (styled like the dashboard's
Select bar). Each LED is one **state** — a quick, at-a-glance health light the base
station evaluates locally before and during a run. There are two *kinds* of state, and
they share the bar:

- **ping** — reachability of an **off-fleet** link (the gateway the station sits behind,
  an upstream target, a peer device). 🟢 green = replied, 🔴 red = no reply, ⚪ gray =
  not checked yet.
- **cmd** — the **exit code** of a command run on the base station, mapped to a named
  color. Any color in the palette: green · yellow · red · blue · purple · orange · gray.

These are **not** fleet nodes (principle **P2**): `ccFleet` never ingests the nodes'
own data. They are base-station-local checks — a ping, or a small command.

## Where the states live (config over code, P8)

Everything is operator-editable from the **Config** page under the **States** root — no
source change. Two example files ship; add, remove or retune freely.

**Ping links** (`networks.yaml`):

```yaml
networks:
  poll_interval: 5        # seconds between checks
  ping_timeout: 1         # seconds to wait for a reply
  links:
    - { key: link1, label: Gateway,     host: 10.0.0.1,  hint: the gateway the base station sits behind }
    - { key: link2, label: Upstream,    host: 10.0.0.2,  hint: an upstream reachability target }
    - { key: link3, label: Peer device, host: 10.0.0.50, hint: a peer device that must be reachable }
```

**Command-driven states** (`stateA.yaml`) — run a command, map its exit code to a color
with `return_colors` (falling back to `default_color`):

```yaml
states:
  poll_interval: 10       # seconds between checks (heavier than a ping)
  timeout: 5              # per-command timeout
  probes:
    - key: disk
      label: Disk
      cmd: "[ \"$(df --output=pcent / | tr -dc '0-9')\" -lt 90 ]"
      return_colors:
        0: green          # under 90% used
        1: red            # 90%+ used
      default_color: yellow   # any other exit code
      hint: root filesystem under 90% used
```

A save is **validated** (`key` a bare token + unique, colors from the palette, `host` a
valid IP/host), **backed up**, **hot-reloaded** (the bar re-checks at once) and
**audited** — exactly like fleet/profile/command edits.

## How it works

```
networks/  ──► core/networks.py  (ping model)  ┐
   stateA.yaml ─► core/states.py (cmd model)    ├─► core/states.StateRegistry
   networks.yaml                                ┘        (one ordered indicator list)
                                                              │
                                                  core/state_monitor.py (I/O shell)
                                                  every poll: ping each host /
                                                  run each cmd, in parallel
                                                              ▼
                              SyncManager.broadcast_states_status ──► socket
                              "states_status" ──► the States bar (one LED each)
```

`GET /api/states` seeds the bar on page load; live updates then arrive over the
`states_status` SocketIO event. A ping is `ping -c 1 -w <timeout> <host>` (the host is a
config-validated bare token passed as an argv element — no shell). A cmd state runs via
the base-station local-exec path (`core/local_exec.py`).

## Trust model

A cmd state's `cmd` runs arbitrary shell **on the base station** as the app user — the
same deliberate config-over-code posture as local custom commands (closed LAN, trusted
operators, full audit), not an oversight. Keys stay bare tokens and colors are
allow-listed so a typo is a line-numbered error rather than a silently dark LED.

## Mock / dry-run

Under `--mock` and `--dry-run` **nothing is pinged or run** — every state reports its
healthy color (green for ping, the exit-0 color for cmd), so the mock lights the whole
bar without touching the network or the base station. cmd states are additionally held
**neutral (gray)** when base-station local exec is disabled (`--no-local-commands`),
since they execute shell here — the higher-blast-radius path.

> If `ping` needs elevated privileges on a given box and is refused, the affected LED
> shows **red** (treated as "no reply") rather than erroring — a safe degradation. A cmd
> that errors falls back to its `default_color`.
