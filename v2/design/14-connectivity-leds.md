---
title: "Connectivity LEDs"
order: 14
summary: "The top-bar LEDs are base-station ping checks for off-fleet links, driven by operator-editable networks.yaml; green = reachable, red = no reply."

---

# Connectivity LEDs (the top-bar status lights)

## What they are

The header carries a small status LED per configured link — by default **Gateway**,
**Upstream** and **Peer device** — so an operator can tell, at a glance and before a
run, that the *base station itself* is wired up:

- 🟢 **green** — the link replied to a ping (reachable)
- 🔴 **red** — no reply (down / wrong IP / cable out)
- ⚪ **gray** — not checked yet (the first poll hasn't landed)

These are **off-fleet** links — the gateway the station sits behind, an upstream
reachability target, and any other device that must be present before a run. They
are **not** fleet nodes (principle **P2**): `ccFleet` never ingests the nodes' own
data. The LEDs are plain ICMP reachability checks the base station runs **locally**.

## Where the addresses live (config over code, P8)

Everything is operator-editable from the **Config** page — no source change:

```yaml
# networks/networks.yaml
networks:
  poll_interval: 5        # seconds between checks
  ping_timeout: 1         # seconds to wait for a reply
  links:
    - { key: link1, label: Gateway,     host: 10.0.0.1,  hint: the gateway the base station sits behind }
    - { key: link2, label: Upstream,    host: 10.0.0.2,  hint: an upstream reachability target }
    - { key: link3, label: Peer device, host: 10.0.0.50, hint: a peer device that must be reachable }
```

Edit the addresses to match your site. A save is **validated** (`key` must be a bare
token and unique, `host` a valid IP/host), **backed up**, **hot-reloaded** (the LEDs
re-poll at once) and **audited** — exactly like fleet/profile/command edits. Add or
remove a link to add or remove an LED; there is nothing special about three.

## How it works

```
networks.yaml ──► core/networks.py (pure model: parse + validate)
                        │
                  core/net_monitor.py  (ping poller, I/O shell)
                        │  every poll_interval, ping each host in parallel
                        ▼
                  SyncManager.broadcast_net_status ──► socket "net_status" ──► top-bar LEDs
```

`GET /api/networks` seeds the LEDs on page load; live updates then arrive over the
`net_status` SocketIO event. The ping is `ping -c 1 -w <timeout> <host>` — the host
is a config-validated bare token passed as an argv element (no shell), so there is no
injection surface.

## Mock / dry-run

Under `--mock` and `--dry-run` **nothing is pinged** — every link simply reports
**up** (green), so the mock lights the whole UI without touching the network. This
mirrors the echo-only discipline of local custom commands. In a real run (no
`--mock`/`--dry-run`) the pings are live and the LEDs reflect actual reachability.

> If `ping` needs elevated privileges on a given box and is refused, the affected LED
> shows **red** (treated as "no reply") rather than erroring — a safe degradation.
