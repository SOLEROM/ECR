# `gates/` — the operator-editable health gates (config over code, P8)

Each `*.yaml` here is **one gate** — a colored readiness cell shown on every node card and
on the node detail page. Gates are config, not code: edit them from the **Config** page
(the **Gates** root) and saves are **validated → backed up → hot-reloaded → audited** with
no restart. Add a 5th gate by adding a 5th file; the cell appears on reload.

The gates roll up into each node card's color (worst wins) and into the `GATE_CHANGED`
audit event + a session-log line on every color flip.

## File shape

```yaml
gate:
  key: A                 # bare token, unique across files; also the display order tiebreak
  label: reach           # operator-facing label on the cell
  kind: reach            # reach | process | metric
  on: roleA              # base | roleA | roleB  (which target the check runs against)
  variants: [B]          # optional: only evaluate for these variants (omit ⇒ all)
  timeout: 5             # per-check timeout (seconds)
  interval: 5            # refresh cadence (seconds)
  order: 0               # optional sort key
  hint: ...              # tooltip
```

## The three kinds

**`reach`** — is a role reachable?
```yaml
  method: ssh            # ssh = control-plane connect (truth); ping = ICMP-ping `host`
  host: "{HOST_A}"       # ping target (per-node {param}); only used when method: ping
  colors: { up: green, down: red }
```
An `ssh` reach gate is also the **short-circuit**: if the role won't connect, the role's
other gates fail immediately instead of stacking timeouts.

**`process`** — a list of processes that must be running:
```yaml
  check: "pgrep -f {pattern} >/dev/null 2>&1"   # exit 0 ⇒ up; {pattern}/{name} per entry
  processes:
    - { name: serviceA, pattern: serviceA, mandatory: true }
    - { name: serviceC, pattern: serviceC, mandatory: true, variants: [B] }
  colors: { all_up: green, optional_down: yellow, mandatory_down: red }
```

**`metric`** — run a command, extract fields, pick the first matching level → its color:
```yaml
  cmd: "..."
  parse: regex                                  # regex | json
  fields:
    - { name: value, pattern: 'value=(\d+)', type: int }   # regex: group(1)
    # - { name: sats, key: gps.sats, type: int }           # json: dotted key path
  detail: "value={value}"                       # {field} placeholders
  levels:                                        # first match wins
    - { when: { value: ">=3" }, color: green }
    - { default: true,          color: red, detail: "no/low check" }
```
Conditions: `">=n"` `"<=n"` `">n"` `"<n"` `"==n"`, ranges `"a..b"`, bool `true`/`false`,
literal `"==text"`. Colors: `green · yellow · red · blue · purple · orange · gray`
(green/blue/purple → ok, yellow/orange → warn, red → fail, gray → na for the card rollup).

## Mock (demo) behavior

Under `--mock`/`--dry-run` no command is run; a small simulate hook keys off the in-memory
fleet: `reach` follows reachability, `process` follows the simulated daemons (so a bring-up
flips the proc gate green), and a `metric` gate's optional `mock` block drives its demo:
```yaml
  mock: { up_when: serviceB, healthy: { value: 3 } }   # healthy once serviceB is up
```

## Trust model

A gate's `cmd`/`check` runs operator-authored shell on the target — the same deliberate
config-over-code posture as custom commands and cmd-states (closed LAN, audited; the local
`base`/`ping` path is echo-only under `--mock`/`--dry-run` and disabled by
`--no-local-commands`). Keys and hosts are validated as bare tokens; an unknown color is a
line-numbered error on save, not a dark cell.
