"""
Config-driven health gates for ccflet (config over code, P8) — the **Gates** engine.

A **gate** is one per-node readiness check shown as a colored cell on every node card /
detail. Gates are operator-editable config — the Config-page **Gates** root, the
``gates/`` directory on disk — so the operator retunes a gate's host, process list,
command, thresholds and refresh cadence without touching the code (P8). This module is
the **generic engine** (template-level, like ``core/states.py``): it parses + validates
a directory of one-gate-per-file YAML into ``GateSpec`` objects, and provides the pure
field-extraction / level-evaluation / color→severity helpers the orchestrator's I/O shell
calls. It is the config-driven replacement for the old hard-coded ``domain/gates.py``
``compute_gates``.

Three gate **kinds**, each declaring *what to run, where, and how the result maps to a
color*:

  - **reach**   — is a role reachable? (``method: ssh`` connect, or ``ping`` a host)
  - **process** — a list of processes that must be running (each mandatory / optional,
    optionally variant-scoped); folded to a color by the mandatory flags.
  - **metric**  — run a command, extract numeric/bool **fields**, and pick the first
    matching **level** (``when`` conditions) → its color.

Configs speak **colors** (the same named set as States:
``green/yellow/red/blue/purple/orange/gray``); the engine derives a **severity**
(``ok/warn/fail/na``) for the existing card rollup (``core/status.overall_gate``) so the
dashboard coloring and the Compiler acceptance gate (which reads ``gate.state``) are
unchanged. A ``GateResult`` therefore carries **both** ``color`` and ``state``.

**Pure:** parsing / validation / field extraction / condition evaluation are functions of
``(dict|text) -> model`` and unit-tested with no network or subprocess
(``tests/test_gates_config.py``). The transport (connect / SSH exec / local exec / ping)
is the thin I/O shell in ``core/orchestrator.py``; the ``--mock`` producer is
``domain/mock_rules.gate_mock``.

Trust model: a gate's ``cmd``/``check`` runs operator-authored shell on the target — the
same deliberate config-over-code posture as custom commands and cmd-states (closed LAN,
audited, echo-only under ``--mock``/``--dry-run``). Keys + hosts are bare tokens; colors
are validated against the allow-list so a typo is a line-numbered error, not a dark cell.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .networks import KEY_RE, HOST_RE
from .states import STATE_COLORS, normalize_color
from .status import OK, WARN, FAIL, NA

GATE_KINDS = ("reach", "process", "metric")
ROLES = ("base", "roleA", "roleB")
REACH_METHODS = ("ssh", "ping")
FIELD_TYPES = ("int", "float", "bool", "str")

DEFAULT_TIMEOUT = 6.0     # per-check timeout (seconds)
DEFAULT_INTERVAL = 5.0    # refresh cadence (seconds)
DEFAULT_CHECK = "pgrep -f {pattern} >/dev/null 2>&1"   # process kind: exit 0 ⇒ up

# color → severity (the old gate vocabulary), so the card rollup / acceptance gate are
# unchanged: configs only pick colors, the engine derives the ok/warn/fail/na the rest of
# the system already understands.
COLOR_SEVERITY: Dict[str, str] = {
    "green": OK, "blue": OK, "purple": OK,
    "yellow": WARN, "orange": WARN,
    "red": FAIL,
    "gray": NA,
}


def color_to_severity(color: str) -> str:
    """Map a named gate color to its ``ok/warn/fail/na`` severity (gray → na)."""
    return COLOR_SEVERITY.get(color, OK)


# --- the per-app config models ----------------------------------------------
@dataclass(frozen=True)
class ProcessSpec:
    """One process a ``process`` gate requires."""
    name: str
    pattern: str
    mandatory: bool = True
    variants: Optional[Tuple[str, ...]] = None   # None ⇒ all variants


@dataclass(frozen=True)
class FieldSpec:
    """One field a ``metric`` gate extracts from its command output."""
    name: str
    pattern: Optional[str] = None    # regex (parse: regex) — group(1) is the value
    key: Optional[str] = None        # json key path (parse: json), e.g. "gps.sats"
    type: str = "str"


@dataclass(frozen=True)
class Level:
    """One ``metric`` level: a set of ``when`` conditions → a color (+ optional detail).
    A level with ``default=True`` always matches (the catch-all)."""
    color: str
    when: Dict[str, Any] = field(default_factory=dict)
    detail: Optional[str] = None
    default: bool = False


@dataclass(frozen=True)
class GateSpec:
    """One operator-defined gate (one ``gates/*.yaml`` file)."""
    key: str
    label: str
    kind: str
    on: str                                  # base | roleA | roleB
    variants: Optional[Tuple[str, ...]]      # None ⇒ all variants
    timeout: float
    interval: float
    hint: str = ""
    order: int = 0
    mock: Dict[str, Any] = field(default_factory=dict)   # simulate-only (read by mock_rules)
    # reach:
    method: str = "ssh"
    host: Optional[str] = None
    colors: Dict[str, str] = field(default_factory=dict)
    # process:
    check: str = DEFAULT_CHECK
    processes: Tuple[ProcessSpec, ...] = ()
    # metric:
    cmd: Optional[str] = None
    parse: str = "regex"
    fields: Tuple[FieldSpec, ...] = ()
    levels: Tuple[Level, ...] = ()
    detail: Optional[str] = None

    def applies_to(self, variant: str) -> bool:
        """Is this gate evaluated for a node in ``variant``? (``variants: null`` ⇒ all)."""
        return self.variants is None or variant in self.variants

    def to_meta(self) -> Dict[str, Any]:
        """What the UI needs to render the cell + tooltip (operator-authored → textContent)."""
        return {"key": self.key, "label": self.label, "kind": self.kind,
                "on": self.on, "hint": self.hint,
                "variants": list(self.variants) if self.variants is not None else None}


# --- parsing / validation (pure) --------------------------------------------
def _as_variants(v: Any, where: str) -> Optional[Tuple[str, ...]]:
    if v is None:
        return None
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise ValueError(f"{where}: 'variants' must be a list of variant tokens")
    return tuple(v)


def _pos_number(v: Any, where: str, what: str, default: float) -> float:
    try:
        n = float(default if v is None else v)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {what} must be a number")
    if n <= 0:
        raise ValueError(f"{where}: {what} must be > 0")
    return n


def _reach_from_dict(d: Dict[str, Any], g: GateSpec, where: str) -> GateSpec:
    method = str(d.get("method", "ssh")).strip().lower()
    if method not in REACH_METHODS:
        raise ValueError(f"{where}: reach 'method' must be one of {REACH_METHODS}")
    host = d.get("host")
    if host is not None:
        host = str(host)
        # may carry {param}; the literal (non-{param}) part must be a host token.
        bare = re.sub(r"\{\w+\}", "x", host)
        if not HOST_RE.match(bare):
            raise ValueError(f"{where}: reach 'host' {host!r} is not a valid host/IP token")
    colors_raw = d.get("colors") or {}
    if not isinstance(colors_raw, dict):
        raise ValueError(f"{where}: reach 'colors' must be a mapping")
    colors = {"up": "green", "down": "red"}
    for k in ("up", "down"):
        if k in colors_raw:
            colors[k] = normalize_color(colors_raw[k], where)
    if method == "ping" and not host:
        raise ValueError(f"{where}: reach 'method: ping' needs a 'host'")
    from dataclasses import replace
    return replace(g, method=method, host=host, colors=colors)


def _process_from_dict(d: Dict[str, Any], g: GateSpec, where: str) -> GateSpec:
    check = d.get("check", DEFAULT_CHECK)
    if not isinstance(check, str) or not check.strip():
        raise ValueError(f"{where}: process 'check' must be a non-empty command string")
    procs_raw = d.get("processes")
    if not isinstance(procs_raw, list) or not procs_raw:
        raise ValueError(f"{where}: process gate needs a non-empty 'processes' list")
    procs: List[ProcessSpec] = []
    seen = set()
    for p in procs_raw:
        if not isinstance(p, dict):
            raise ValueError(f"{where}: each process must be a mapping")
        name = str(p.get("name") or "").strip()
        if not KEY_RE.match(name):
            raise ValueError(f"{where}: process name {name!r} must match {KEY_RE.pattern}")
        if name in seen:
            raise ValueError(f"{where}: duplicate process {name!r}")
        seen.add(name)
        procs.append(ProcessSpec(
            name=name, pattern=str(p.get("pattern") or name),
            mandatory=bool(p.get("mandatory", True)),
            variants=_as_variants(p.get("variants"), f"{where} process {name!r}")))
    colors_raw = d.get("colors") or {}
    if not isinstance(colors_raw, dict):
        raise ValueError(f"{where}: process 'colors' must be a mapping")
    colors = {"all_up": "green", "optional_down": "yellow", "mandatory_down": "red"}
    for k in ("all_up", "optional_down", "mandatory_down"):
        if k in colors_raw:
            colors[k] = normalize_color(colors_raw[k], where)
    from dataclasses import replace
    return replace(g, check=check, processes=tuple(procs), colors=colors)


def _field_from_dict(d: Any, where: str, parse: str) -> FieldSpec:
    if not isinstance(d, dict):
        raise ValueError(f"{where}: each field must be a mapping")
    name = str(d.get("name") or "").strip()
    if not KEY_RE.match(name):
        raise ValueError(f"{where}: field name {name!r} must match {KEY_RE.pattern}")
    ftype = str(d.get("type", "str")).strip().lower()
    if ftype not in FIELD_TYPES:
        raise ValueError(f"{where}: field {name!r} type must be one of {FIELD_TYPES}")
    if parse == "regex":
        pattern = d.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"{where}: field {name!r} (parse: regex) needs a 'pattern'")
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"{where}: field {name!r} pattern is not valid regex: {e}")
        return FieldSpec(name=name, pattern=pattern, type=ftype)
    key = d.get("key") or name
    return FieldSpec(name=name, key=str(key), type=ftype)


def _level_from_dict(d: Any, where: str) -> Level:
    if not isinstance(d, dict):
        raise ValueError(f"{where}: each level must be a mapping")
    is_default = bool(d.get("default", False))
    when = d.get("when") or {}
    if not is_default and not isinstance(when, dict):
        raise ValueError(f"{where}: level 'when' must be a mapping")
    if not is_default and not when:
        raise ValueError(f"{where}: a non-default level needs a 'when' (or 'default: true')")
    color = d.get("color")
    if color is None:
        raise ValueError(f"{where}: each level needs a 'color'")
    return Level(color=normalize_color(color, where),
                 when=dict(when) if isinstance(when, dict) else {},
                 detail=d.get("detail"), default=is_default)


def _metric_from_dict(d: Dict[str, Any], g: GateSpec, where: str) -> GateSpec:
    cmd = d.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError(f"{where}: metric gate needs a non-empty 'cmd'")
    parse = str(d.get("parse", "regex")).strip().lower()
    if parse not in ("regex", "json"):
        raise ValueError(f"{where}: metric 'parse' must be 'regex' or 'json'")
    fields_raw = d.get("fields") or []
    if not isinstance(fields_raw, list):
        raise ValueError(f"{where}: metric 'fields' must be a list")
    fields = tuple(_field_from_dict(f, where, parse) for f in fields_raw)
    levels_raw = d.get("levels") or []
    if not isinstance(levels_raw, list) or not levels_raw:
        raise ValueError(f"{where}: metric gate needs a non-empty 'levels' list")
    levels = tuple(_level_from_dict(l, where) for l in levels_raw)
    if not any(l.default for l in levels):
        raise ValueError(f"{where}: metric 'levels' must end with a '{{default: true, color}}'")
    from dataclasses import replace
    return replace(g, cmd=cmd, parse=parse, fields=fields, levels=levels,
                   detail=d.get("detail"))


def _get_on(block: Dict[str, Any]) -> Any:
    """Read the ``on`` field, tolerating the YAML 1.1 gotcha where a bare ``on:`` key is
    parsed as the boolean ``True`` (the operator naturally writes ``on: roleA``)."""
    if "on" in block:
        return block["on"]
    if True in block:                     # PyYAML parsed the bare `on:` key as True
        return block[True]
    return None


def gate_from_dict(d: Dict[str, Any], source: str = "<dict>") -> GateSpec:
    """Parse + validate one gate's ``gate:`` block (or a bare dict) → ``GateSpec``."""
    block = d.get("gate", d) if isinstance(d, dict) else None
    if not isinstance(block, dict):
        raise ValueError(f"{source}: a gate file must hold a 'gate' mapping")
    key = str(block.get("key") or "").strip()
    if not KEY_RE.match(key):
        raise ValueError(f"{source}: gate key {key!r} must match {KEY_RE.pattern}")
    where = f"{source}: gate {key!r}"
    kind = str(block.get("kind") or "").strip().lower()
    if kind not in GATE_KINDS:
        raise ValueError(f"{where}: 'kind' must be one of {GATE_KINDS}")
    on = str(_get_on(block) or ("base" if kind == "reach" else "roleA")).strip()
    if on not in ROLES:
        raise ValueError(f"{where}: 'on' must be one of {ROLES}")
    mock = block.get("mock") or {}
    if not isinstance(mock, dict):
        raise ValueError(f"{where}: 'mock' must be a mapping")
    base = GateSpec(
        key=key, label=str(block.get("label") or key), kind=kind, on=on,
        variants=_as_variants(block.get("variants"), where),
        timeout=_pos_number(block.get("timeout"), where, "timeout", DEFAULT_TIMEOUT),
        interval=_pos_number(block.get("interval"), where, "interval", DEFAULT_INTERVAL),
        hint=str(block.get("hint") or ""), order=int(block.get("order", 0) or 0),
        mock=dict(mock))
    if kind == "reach":
        return _reach_from_dict(block, base, where)
    if kind == "process":
        return _process_from_dict(block, base, where)
    return _metric_from_dict(block, base, where)


def gate_file_from_dict(data: Dict[str, Any], source: str = "<dict>") -> GateSpec:
    """Config-store entry point: validate one gate file's parsed YAML. Raises ValueError
    with a useful message so the Config page reports it on save."""
    if not isinstance(data, dict):
        raise ValueError(f"{source}: a gate file must be a mapping")
    return gate_from_dict(data, source=source)


# --- field extraction + level evaluation (pure) -----------------------------
def _cast(value: str, ftype: str) -> Any:
    if ftype == "int":
        return int(re.sub(r"[^\d-]", "", value) or "0")
    if ftype == "float":
        return float(re.findall(r"-?\d+(?:\.\d+)?", value)[0]) if re.findall(
            r"-?\d+(?:\.\d+)?", value) else 0.0
    if ftype == "bool":
        return str(value).strip().lower() not in ("", "0", "false", "no", "off")
    return value


def _json_get(obj: Any, key: str) -> Any:
    cur = obj
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def extract_fields(text: str, fields: Tuple[FieldSpec, ...], parse: str) -> Dict[str, Any]:
    """Pull each declared field out of a metric command's output. Missing fields are
    omitted (so a level condition on them simply doesn't match)."""
    out: Dict[str, Any] = {}
    text = text or ""
    if parse == "json":
        import json
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return out
        for f in fields:
            raw = _json_get(data, f.key or f.name)
            if raw is not None:
                try:
                    out[f.name] = _cast(str(raw), f.type) if f.type != "str" else raw
                except (ValueError, IndexError):
                    pass
        return out
    for f in fields:
        m = re.search(f.pattern, text) if f.pattern else None
        if not m:
            continue
        raw = m.group(1) if m.groups() else m.group(0)
        try:
            out[f.name] = _cast(raw, f.type)
        except (ValueError, IndexError):
            pass
    return out


_NUM_OP_RE = re.compile(r"^(>=|<=|==|>|<)\s*(-?\d+(?:\.\d+)?)$")
_RANGE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\.\.(-?\d+(?:\.\d+)?)$")


def match_condition(value: Any, cond: Any) -> bool:
    """Does a field ``value`` satisfy one ``when`` condition? Supports bool, numeric
    compares (``">=n"``…), ranges (``"a..b"``), ``"==v"`` and literal equality. A missing
    field (``value is None``) only matches an explicit ``false``/``== ''`` style condition."""
    if isinstance(cond, bool):
        return bool(value) == cond
    if isinstance(cond, (int, float)):
        return value is not None and value == cond
    if isinstance(cond, str):
        s = cond.strip()
        m = _NUM_OP_RE.match(s)
        if m:
            if not isinstance(value, (int, float)):
                return False
            op, n = m.group(1), float(m.group(2))
            return {">=": value >= n, "<=": value <= n, ">": value > n,
                    "<": value < n, "==": value == n}[op]
        r = _RANGE_RE.match(s)
        if r:
            if not isinstance(value, (int, float)):
                return False
            lo, hi = float(r.group(1)), float(r.group(2))
            return lo <= value <= hi
        if s.startswith("=="):
            return value is not None and str(value) == s[2:].strip()
        return value is not None and str(value) == s
    return False


def evaluate_levels(fields: Dict[str, Any], levels: Tuple[Level, ...]) -> Level:
    """First level whose every ``when`` condition matches (a ``default`` level always
    matches). Assumes a default level exists (enforced at parse time)."""
    for lvl in levels:
        if lvl.default:
            return lvl
        if all(match_condition(fields.get(fname), cond) for fname, cond in lvl.when.items()):
            return lvl
    return levels[-1]


def render_detail(template: Optional[str], fields: Dict[str, Any]) -> str:
    """Fill ``{field}`` placeholders in a detail template; missing fields → ``—``."""
    if not template:
        return ""
    return re.sub(r"\{(\w+)\}",
                  lambda m: str(fields.get(m.group(1), "—")), template)


# --- registry ---------------------------------------------------------------
class GateRegistry:
    """All ``gates/*.yaml`` under the gates directory → one ordered ``GateSpec`` list.

    Loaded from a directory: every ``*.yaml`` is one gate, ordered by ``order`` then
    filename. Opt-in by shipping files — an empty/absent directory yields no gates.

    Reloaded **in place** (``reload``) like ``StateRegistry`` / ``Fleet`` so the
    orchestrator — which holds this same reference — picks up Config-page edits with no
    restart (P8). A single malformed file is skipped (the rest survive); the Config page
    validates a file *before* it is written, so this is belt-and-suspenders.
    """

    def __init__(self, directory: str):
        self.dir = directory
        self.specs: List[GateSpec] = []
        self.reload()

    def _yaml_files(self) -> List[str]:
        if not os.path.isdir(self.dir):
            return []
        return sorted(
            n for n in os.listdir(self.dir)
            if not n.startswith(".") and n.lower().endswith((".yaml", ".yml"))
            and os.path.isfile(os.path.join(self.dir, n)))

    def reload(self) -> "GateRegistry":
        specs: List[GateSpec] = []
        seen = set()
        for name in self._yaml_files():
            try:
                with open(os.path.join(self.dir, name), "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                spec = gate_file_from_dict(data, source=name)
            except (OSError, ValueError, yaml.YAMLError):
                continue                      # safe degradation: skip a broken file
            if spec.key in seen:              # first file wins on a cross-file key clash
                continue
            seen.add(spec.key)
            specs.append(spec)
        specs.sort(key=lambda s: (s.order, s.key))
        self.specs = specs
        return self

    @property
    def poll_interval(self) -> float:
        """The poll loop ticks at the most-frequent gate's cadence (floored at 1s)."""
        if not self.specs:
            return DEFAULT_INTERVAL
        return max(1.0, min(s.interval for s in self.specs))

    def metas(self) -> List[Dict[str, Any]]:
        return [s.to_meta() for s in self.specs]

    def by_key(self, key: str) -> Optional[GateSpec]:
        for s in self.specs:
            if s.key == key:
                return s
        return None


# --- the per-evaluation result ----------------------------------------------
def gate_result(spec: GateSpec, color: str, detail: str = "",
                fields: Optional[Dict[str, Any]] = None,
                processes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One gate's wire/UI payload. Carries the named ``color`` AND the derived ``state``
    (severity) so the card rollup / acceptance gate keep working. Operator-authored
    strings reach the UI via ``textContent`` (keep the XSS discipline)."""
    return {"key": spec.key, "label": spec.label, "kind": spec.kind, "on": spec.on,
            "color": color, "state": color_to_severity(color), "detail": detail,
            "fields": fields or {}, "processes": processes or []}


def na_result(spec: GateSpec, detail: str = "n/a (variant)") -> Dict[str, Any]:
    """A gate that doesn't apply to the node's current variant → a gray ``na`` cell."""
    return gate_result(spec, "gray", detail)
