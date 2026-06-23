# ccFleet — the demo dream (Layer 1)

> This is the wish list that *is* the template's own demo. Building it reproduces the
> default ccFleet app, so the Compiler's faithfulness is checkable: a clean build must
> still pass `pytest` + `--mock` + `--dry-run`. Fork the template and rewrite this file
> for your own app (a paragraph is enough — everything unspecified inherits a template
> default).

**Mission.** Bring a fleet of remote nodes **up** over SSH, **watch** it, and bring it
**down** — with everything recorded. Strictly an ops/control plane: deploy, daemon
lifecycle, logs, health. The control path to a node is always SSH.

**Shape.** Five nodes. Each node has two roles — **roleA** (the primary, directly
reachable host) and **roleB** (a secondary host reached *through* roleA as a jump). A
node runs three services: **serviceA** and **serviceB** on roleA, and **serviceC** on
roleB. serviceB depends on serviceA, so serviceA starts first; in the "live" variant
serviceC must come up before serviceA.

**Variants.** Each node carries its own **A/B** toggle (per-node runtime state).
Variant **A** is the simple profile (roleA only); variant **B** is the full profile
(roleB + serviceC + extra probes and a sensor check).

**Healthy** (the four gates):
- **A · reach** — roleA reachable (in variant B, also roleB + probe A READY + probe B OK).
- **B · proc** — serviceA + serviceB up (serviceC too in variant B).
- **C · check** — variant B only: a sensor value reads "good" and fresh.
- **D · link** — peers are heard recently (variant B also folds in serviceC link stats).

**Buttons.** The usual deploy / bring-up / tear-down per node and across a selection,
plus a few operator command buttons: a base-station disk check and an "archive old
runs" housekeeping task (local 🖥), and small remote diagnostics (uptime, ping roleB).

**Top-bar LEDs.** Watch three off-fleet links before a run: the gateway, an upstream
target, and a peer device.

**Don'ts.** No auth/RBAC (closed LAN, trusted operators, audit everything). No
provisioning (assume nodes are already reachable). No confirm prompts — the audit log
is the safety net.
