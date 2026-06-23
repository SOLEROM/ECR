"""
The creative stages — **distill** (dream → params) and **expand** (params → sub-parts).

Each returns an editable artifact (the IR you read + correct, R3). Both try the LLM
backend first and fall back to a deterministic template-default draft (R2) so the
pipeline always produces a valid checkpoint, model or no model. Precision +
verification live downstream in the deterministic build + the gate.
"""

import re
from typing import Any, Dict, List, Tuple

import yaml

from . import llm

# --- template defaults (the "high floor" an underspecified dream inherits) ---
_DEFAULT_DEFAULTS = {
    "variant": "A", "algo": "default", "roleA_user": "user", "roleB_user": "root",
    "key_file": "~/.ssh/id_rsa", "deploy_root": "/srv/ccfleet/roleA",
    "ssh_opts": "-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5",
    "stagger": 0.5, "roleB_host_suffix": ".2",
}
_DEFAULT_VARIANT_PARAMS = {
    "A": {"addr": "10.0.0.255", "launcher": "variantA.run", "flag": ""},
    "B": {"addr": "{SUBNET}.255", "launcher": "variantB.run", "flag": "--variant-flag"},
}


# --- distill -----------------------------------------------------------------
def _heuristic_params(dream: str) -> Tuple[Dict[str, Any], List[str]]:
    notes: List[str] = []
    # app name: first markdown heading, else first capitalised word, else MyFleet
    name = "MyFleet"
    m = re.search(r"^#\s+([A-Za-z0-9 _-]{2,40})", dream, re.M)
    if m:
        name = m.group(1).strip().split()[0]
        notes.append(f"app.name inferred from heading: {name!r}")
    else:
        notes.append("app.name not found in dream — defaulted to 'MyFleet' (EDIT)")
    # node count: a number near "node"/"station"/"unit"/"device"
    count = 3
    cm = re.search(r"(\d+)\s+(?:nodes?|stations?|units?|devices?|kiosks?|cameras?)",
                   dream, re.I)
    if cm:
        count = int(cm.group(1))
        notes.append(f"node.count inferred: {count}")
    else:
        notes.append("node.count not found — defaulted to 3 (EDIT)")
    params = {
        "app": {
            "name": name,
            "tagline": "Command & Control",
            "node": {"count": count, "represents": "a fleet node (EDIT)"},
            "roles": {"roleA": "roleA", "roleB": "roleB"},
            "services": {"serviceA": "serviceA", "serviceB": "serviceB",
                         "serviceC": "serviceC"},
            "variants": {"A": "variant A", "B": "variant B"},
            "gates": {"A": "reach", "B": "proc", "C": "check", "D": "link"},
            "defaults": dict(_DEFAULT_DEFAULTS),
            "variant_params": _DEFAULT_VARIANT_PARAMS,
        }
    }
    return params, notes


_DISTILL_PROMPT = """You are the ccFleet Compiler's `distill` stage. Read the DREAM below \
and produce ONLY a YAML document for `layer2.params.yaml` — the few global facts true \
for the whole app. Use this exact shape (omit a gate to drop it; roleB may be null for \
single-host nodes). Fill names from the dream; keep `defaults`/`variant_params` unless \
the dream says otherwise.

```yaml
app:
  name: <AppName>            # a bare token (letters/digits/_/-)
  tagline: "..."
  node: {{ count: <N>, represents: "..." }}
  roles: {{ roleA: <label>, roleB: <label-or-null> }}
  services: {{ serviceA: <label>, serviceB: <label>, serviceC: <label> }}
  variants: {{ A: "...", B: "..." }}
  gates: {{ A: <label>, B: <label>, C: <label>, D: <label> }}
  defaults: {{ roleA_user: ..., deploy_root: ..., stagger: 0.5 }}
  variant_params:
    A: {{ addr: "...", launcher: "...", flag: "" }}
    B: {{ addr: "{{SUBNET}}.255", launcher: "...", flag: "..." }}
```

DREAM:
{dream}
"""


def distill(dream: str, provider: str) -> Tuple[Dict[str, Any], List[str]]:
    """dream → params. LLM if available, else a heuristic template-default draft."""
    if provider == "claude":
        try:
            text = llm.complete(_DISTILL_PROMPT.format(dream=dream), provider)
            body = llm.extract_yaml(text)
            data = yaml.safe_load(body) if body else None
            if isinstance(data, dict) and (data.get("app") or data.get("name")):
                return (data if "app" in data else {"app": data},
                        ["distilled by claude"])
        except llm.LLMUnavailable as e:
            return _heuristic_with_note(dream, str(e))
    return _heuristic_params(dream)


def _heuristic_with_note(dream: str, why: str):
    params, notes = _heuristic_params(dream)
    notes.insert(0, f"claude unavailable ({why}); used offline heuristic")
    return params, notes


# --- expand ------------------------------------------------------------------
def _default_subparts(params: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    a = params.get("app") or params
    notes = ["expanded with template-default sub-parts (EDIT to taste)"]
    subparts = {
        "identity": {
            "extends": "identity",
            "app_name": a.get("name", "MyFleet"),
            "tagline": a.get("tagline", "Command & Control"),
        },
        "host-actions": {
            "extends": "commands.host",
            "add": [
                {"id": "base_disk", "label": "Base-station disk",
                 "group": "Housekeeping", "run": "df -h .", "mode": "live"},
            ],
        },
        "roleA-actions": {
            "extends": "commands.roleA",
            "add": [
                {"id": "uptime", "label": "Uptime", "group": "Diagnostics",
                 "scope": "node", "run": "uptime", "mode": "live"},
            ],
        },
        "networks": {
            "extends": "networks",
            "poll_interval": 5, "ping_timeout": 1,
            "links": [
                {"key": "link1", "label": "Gateway", "host": "10.0.0.1",
                 "hint": "the gateway / router the base station is connected to"},
            ],
        },
    }
    return subparts, notes


_EXPAND_PROMPT = """You are the ccFleet Compiler's `expand` stage. Given the PARAMS \
(global facts) and DREAM, draft the `layer3.subparts/*.yaml` files — one per UI region, \
each a patch (`extends:` + `add:`/`remove:`/overrides) with a `mode:` (live|frozen) per \
item. Cover at least: host-actions, roleA-actions, networks. Output one ```yaml block per \
file, each preceded by a line `# file: <stem>.yaml`.

PARAMS:
{params}

DREAM:
{dream}
"""


def expand(params: Dict[str, Any], dream: str, provider: str
           ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """params → sub-parts. LLM if available, else template-default sub-parts."""
    if provider == "claude":
        try:
            text = llm.complete(
                _EXPAND_PROMPT.format(params=yaml.safe_dump(params), dream=dream),
                provider)
            parsed = _parse_multi_yaml(text)
            if parsed:
                return parsed, ["expanded by claude"]
        except llm.LLMUnavailable:
            pass
    return _default_subparts(params)


def _parse_multi_yaml(text: str) -> Dict[str, Dict[str, Any]]:
    """Parse the `# file: <stem>.yaml` + ```yaml block convention into {stem: data}."""
    out: Dict[str, Dict[str, Any]] = {}
    stem = None
    for chunk in text.split("```"):
        fm = re.search(r"#\s*file:\s*([\w.-]+)", chunk)
        if fm:
            stem = re.sub(r"\.ya?ml$", "", fm.group(1).strip())
        body = chunk
        if body.lstrip().lower().startswith(("yaml", "yml")):
            body = body.split("\n", 1)[1] if "\n" in body else ""
        try:
            data = yaml.safe_load(body)
        except yaml.YAMLError:
            data = None
        if stem and isinstance(data, dict):
            out[stem] = data
            stem = None
    return out
