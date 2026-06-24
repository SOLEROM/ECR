# GUI parts index (`guiPartNN`)

Every distinct, visible/interactive region of the web UI is tagged with a stable
`guiPartNN` id so you can find and restyle it without hunting through markup. Each
part carries `data-guipart="NN"` (and, where it's a single element, `id="guiPartNN"`)
plus a one-line HTML comment. Numbering is global and display-ordered. Parts built
dynamically in JavaScript (cards, event rows, tree files, command buttons) get the
attribute when created — search the `.js`/template for the number.

This is a **template aid**: rename the labels, drop parts you don't need, renumber as
you like. The ids are not used by any logic — they're purely for navigation/restyling.

## Global layout — `base.html` (01–10)

| Part | Region |
|---|---|
| guiPart01 | brand / logo (links home) |
| guiPart02 | main navigation (Dashboard / Sessions / Config / Help) |
| guiPart03 | _(merged into guiPart121 — the run mode is now the colour of the profile pill)_ |
| guiPart04 | States bar — status LEDs (ping links + cmd states); the "States" word re-checks all states on demand |
| guiPart05 | connected-operator roster |
| guiPart06 | operator name input |
| guiPart07 | live connection status |
| guiPart08 | toast notifications |
| guiPart09 | session dock (bottom drawer) |
| guiPart10 | bottom command bar (session chips) |
| guiPart120 | light/dark theme toggle (header, top-right) — numbered out-of-sequence to stay globally unique (base.html renders on every page) |
| guiPart121 | config-profile pill (the editable-YAML set in use; P8) — header, top-right; merges the old run-mode badge (guiPart03): the pill is coloured by run mode (green = live · purple = mock · yellow = dry-run) and labelled with the active profile. It's a button → opens the switch popup (guiPart126); the profile segment updates live on `profile_changed` |
| guiPart126 | config-profile switch popup (P8) — opened from the pill (guiPart121); lists every profile, clicking one switches the live set (`POST /api/config/profile`) and reloads the page. Create a new profile from the Config page (guiPart92) |

## Dashboard — `dashboard.html` (11–40)

| Part | Region |
|---|---|
| guiPart11 | toolbar |
| guiPart12 | selection cluster (All / Clear / Groups) |
| guiPart13 | selection counter |
| guiPart14 | health refresh (the "Select" word — click to re-poll the fleet) |
| guiPart15 | reset card order |
| guiPart16 | grid / tabs / logs view toggle |
| guiPart17 | node grid |
| guiPart18 | node card (one per node) |
| guiPart19 | card header |
| guiPart20 | node selection checkbox |
| guiPart21 | drag-to-reorder grip |
| guiPart22 | node name link |
| guiPart23 | node id |
| guiPart24 | node host (roleA) |
| guiPart25 | health gates row (cells built client-side from /api/gates) |
| guiPart26 | one gate cell (colored by the gate's named color) |
| guiPart27 | metrics row (re-sourced from gate results: process pills + metric fields) |
| guiPart28 | a process up/down pill (rendered per process gate entry) |
| guiPart30 | card footer |
| guiPart31 | per-node variant toggle (A/B) |
| guiPart32 | Deploy button |
| guiPart33 | Bring-up button |
| guiPart34 | Tear-down button |
| guiPart35 | tabs view container |
| guiPart36 | node tab strip |
| guiPart37 | a node tab |
| guiPart38 | node tab health dot |
| guiPart39 | node panes container |
| guiPart40 | a node detail pane (iframe) |

## Node detail — `node.html` (41–62)

| Part | Region |
|---|---|
| guiPart41 | page breadcrumb / title |
| guiPart42 | left column (flex-stacks the group panels: node · gates · states · actions · commands) |
| guiPart43 | node info panel (identity + addressing) |
| guiPart124 | gates panel (node detail) — numbered out-of-sequence to stay globally unique |
| guiPart125 | states panel (node detail: per-node variant toggle + base-station States LEDs) |
| guiPart44 | per-node variant toggle (inside the states panel) |
| guiPart45 | health gates (inside the gates panel) |
| guiPart46 | metrics (inside the gates panel) |
| guiPart47 | actions panel |
| guiPart48 | an action group |
| guiPart49 | sequence buttons |
| guiPart50 | deploy actions |
| guiPart51 | serviceA control |
| guiPart52 | serviceB control |
| guiPart53 | roleB control (variant B) |
| guiPart54 | single-action output |
| guiPart55 | custom commands panel (node scope) |
| guiPart56 | a custom-command group (built in JS) |
| guiPart57 | a custom-command button (built in JS) |
| guiPart58 | right column (live logs) |
| guiPart59 | log tab strip |
| guiPart60 | a log tab (built in JS) |
| guiPart61 | log stream state |
| guiPart62 | live log terminal (xterm) |

## Sessions list — `sessions.html` (63–74)

| Part | Region |
|---|---|
| guiPart63 | page toolbar |
| guiPart64 | new-session input + button |
| guiPart65 | sessions table |
| guiPart66 | pin toggle |
| guiPart67 | session name link |
| guiPart68 | status badge |
| guiPart69 | variant / algo / node-count cells |
| guiPart70 | created timestamp |
| guiPart71 | rename |
| guiPart72 | close |
| guiPart73 | export ZIP |
| guiPart74 | delete |

## Session view — `session_view.html` (75–92)

| Part | Region |
|---|---|
| guiPart75 | page header with status |
| guiPart76 | session toolbar |
| guiPart77 | session id |
| guiPart78 | session metadata (variant / algo / node-count / timestamps) |
| guiPart79 | rename |
| guiPart80 | note input |
| guiPart81 | note submit |
| guiPart82 | close session |
| guiPart83 | export ZIP |
| guiPart84 | custom commands panel (fleet scope) |
| guiPart85 | custom-command button group (built in JS) |
| guiPart86 | event timeline container |
| guiPart87 | event count |
| guiPart88 | an event row (built in JS) |
| guiPart89–92 | event row sub-fields: time / type / user / detail (built in JS) |

## Config editor — `config.html` (92–108)

| Part | Region |
|---|---|
| guiPart92 | config profile row (P8) — shows the active editable-YAML set + ＋ new (clone-and-edit). Switching *between* profiles is done from the header pill's popup (guiPart121/126); every profile lives under `yamls/<name>/` (default is `yamls/default/`) |
| guiPart93 | config layout |
| guiPart94 | config sidebar |
| guiPart95 | filter input |
| guiPart96 | refresh tree |
| guiPart97 | config file tree |
| guiPart98 | a root directory (built in JS) |
| guiPart99 | a config file item (built in JS) |
| guiPart100 | editor header |
| guiPart101 | file path |
| guiPart102 | kind chip |
| guiPart103 | dirty state |
| guiPart104 | validate |
| guiPart105 | revert |
| guiPart106 | save |
| guiPart107 | text editor |
| guiPart108 | validation messages |

## Help / design docs — `help.html` (109–119)

| Part | Region |
|---|---|
| guiPart109 | page title |
| guiPart110 | help sidebar |
| guiPart111 | filter input |
| guiPart112 | refresh tree |
| guiPart113 | doc tree |
| guiPart114 | a tree directory (built in JS) |
| guiPart115 | a tree file (built in JS) |
| guiPart116 | doc viewer header |
| guiPart117 | doc title |
| guiPart118 | doc path |
| guiPart119 | rendered markdown content |

## Logs view — `dashboard.html` (121–123)

| Part | Region |
|---|---|
| guiPart121 | logs view container |
| guiPart122 | logs window grid |
| guiPart123 | a log window (built in JS) |
