"""
domain/identity.py — operator-facing **labels** for this app.

Labels are data; identifiers are not. The Compiler fills these names from the
``system/`` spec so cards, buttons and gates read in the app's own language, while
the load-bearing **brand tokens stay** — ``ccflet`` / ``CCFlet`` / ``CCFLET_`` /
``/tmp/ccflet`` / ``X-CCFlet-User`` are wired into the wire protocol, env, supervisor
paths and audit, and must never be renamed (CLAUDE.md §8).

The engine exposes ``IDENTITY`` to every template (a Flask context processor) and the
build stage also writes the matching values into fenced ``<!-- GEN:identity --> …
<!-- /GEN -->`` regions. This module holds the default *ccFleet demo* identity.
"""

IDENTITY = {
    # --- brand / app name (a label; the brand *tokens* in code stay) ---------
    "app_name": "ccFleet",
    "brand_lead": "cc",        # wordmark: lead + accented tail (cc·Fleet)
    "brand_accent": "Fleet",
    "tagline": "Command & Control",
    "node_represents": "a fleet node",

    # --- role labels (structural keys roleA/roleB stay; these are display) ---
    "roles": {"roleA": "roleA", "roleB": "roleB"},

    # --- service labels (keys serviceA/B/C stay; these are display) ----------
    "services": {"serviceA": "serviceA", "serviceB": "serviceB", "serviceC": "serviceC"},

    # --- variant labels (keys A/B stay) -------------------------------------
    "variants": {"A": "variant A", "B": "variant B"},

    # --- health gate labels (ordered; keys A–D stay) ------------------------
    "gates": [
        {"key": "A", "label": "reach"},
        {"key": "B", "label": "proc"},
        {"key": "C", "label": "check"},
        {"key": "D", "label": "link"},
    ],
}


def gate_label(key: str) -> str:
    """The operator-facing label for one gate key (falls back to the key)."""
    for g in IDENTITY["gates"]:
        if g["key"] == key:
            return g["label"]
    return key
