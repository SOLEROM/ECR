"""
The overridable sub-part catalog — the menu of Layer-3 patches + their defaults.

``scaffold <part>`` dumps a part's current default as a ready-to-edit
``layer3.subparts/<part>.yaml`` (so you edit a filled file, not a blank one — R3). Skip
a part entirely and you inherit the template default (R2). The human-readable index is
``system/catalog.md``; this is the machine source used by ``scaffold``.
"""

from typing import Any, Dict, Tuple

# part stem -> (one-line description, default Layer-3 body, mode hint)
PARTS: Dict[str, Tuple[str, Dict[str, Any], str]] = {
    "identity": (
        "operator-facing labels (app name, brand, gate/role/service labels)",
        {"extends": "identity", "app_name": "MyFleet", "tagline": "Command & Control",
         "brand_lead": "My", "brand_accent": "Fleet"},
        "live",
    ),
    "host-actions": (
        "LOCAL (base-station) command buttons 🖥 — commands_host.yaml",
        {"extends": "commands.host",
         "add": [{"id": "base_disk", "label": "Base-station disk",
                  "group": "Housekeeping", "run": "df -h .", "mode": "live"}]},
        "live",
    ),
    "roleA-actions": (
        "REMOTE roleA command buttons 🛰 — commands_roleA.yaml",
        {"extends": "commands.roleA",
         "add": [{"id": "uptime", "label": "Uptime", "group": "Diagnostics",
                  "scope": "node", "run": "uptime", "mode": "live"}]},
        "live",
    ),
    "roleB-actions": (
        "REMOTE roleB command buttons 🛰 — commands_roleB.yaml",
        {"extends": "commands.roleB",
         "add": [{"id": "roleb_ping", "label": "Ping roleB", "group": "Diagnostics",
                  "scope": "node", "run": "ping -c1 {HOST_B}", "mode": "live"}]},
        "live",
    ),
    "networks": (
        "base-station connectivity LEDs (top bar) — networks.yaml",
        {"extends": "networks", "poll_interval": 5, "ping_timeout": 1,
         "links": [{"key": "link1", "label": "Gateway", "host": "10.0.0.1",
                    "hint": "the gateway / router the base station is connected to"}]},
        "live",
    ),
    "sequences": (
        "variant-aware bring-up / tear-down / deploy order (FROZEN — code)",
        {"extends": "sequences",
         "deploy": {"steps": [{"role": "roleA", "action": "deploy_serviceB"},
                              {"role": "roleA", "action": "deploy_serviceA"}],
                    "build_steps": [{"role": "roleA", "action": "serviceA_build"}]},
         "bring_up": {"variants": {
             "A": [{"role": "roleA", "start": "serviceA_start", "status": "serviceA_status"},
                   {"role": "roleA", "start": "serviceB_start", "status": "serviceB_status"}],
             "B": [{"role": "roleB", "start": "serviceC_start", "status": "serviceC_status"},
                   {"role": "roleA", "start": "serviceA_start", "status": "serviceA_status"},
                   {"role": "roleA", "start": "serviceB_start", "status": "serviceB_status"}]}},
         "tear_down": {"variants": {
             "A": [{"role": "roleA", "action": "serviceB_stop"},
                   {"role": "roleA", "action": "serviceA_stop"}],
             "B": [{"role": "roleA", "action": "serviceB_stop"},
                   {"role": "roleA", "action": "serviceA_stop"},
                   {"role": "roleB", "action": "serviceC_stop"}]}},
         "invariants": {"bring_up": {"A": ["serviceA_start", "serviceB_start"],
                                     "B": ["serviceC_start", "serviceA_start", "serviceB_start"]},
                        "tear_down": {"A": ["serviceB_stop", "serviceA_stop"],
                                      "B": ["serviceB_stop", "serviceA_stop", "serviceC_stop"]}}},
        "frozen",
    ),
    "gate-c": (
        "GATE C — the per-variant-B sensor/value check (FROZEN — code)",
        {"extends": "gate.C", "label": "check", "applies_to_variant": "B",
         "thresholds": {"CHECK_GOOD": 3, "CHECK_FRESH_S": 1.0},
         "parse": "TODO: describe how to parse the value (LLM codegen)",
         "good": "TODO: describe when the value is good"},
        "frozen",
    ),
    "gate-d": (
        "GATE D — link/peer liveness (FROZEN — code)",
        {"extends": "gate.D", "label": "link",
         "thresholds": {"LINK_FRESH_MS": 1000, "SERVICEC_MIN_UP": 15}},
        "frozen",
    ),
    "docs": (
        "Help (design/) tree — generated front page + glossary, app-name relabeling",
        {"extends": "docs",
         "generate_about": True,    # write design/00-about.md (app intro + glossary)
         "relabel_app_name": True,  # substitute the display app name across the tree
         "substitutions": {},       # extra literal from->to display-token pairs (advanced)
         "exclude": []},            # design/ relpaths to leave exactly as the template
        "live",
    ),
}


def part_default(name: str):
    """Return ``(description, body, mode)`` for a catalog part, or raise KeyError."""
    return PARTS[name]


def index() -> str:
    """A short text index of the catalog (for ``compile.sh scaffold`` with no part)."""
    lines = ["Overridable sub-parts (scaffold one with `compile.sh scaffold <part>`):", ""]
    for name, (desc, _body, mode) in PARTS.items():
        lines.append(f"  {name:<16} [{mode:<6}] {desc}")
    return "\n".join(lines)
