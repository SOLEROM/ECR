"""
States subsystem for ccflet (config over code) — the top-bar status LEDs.

A **state** is one status indicator shown as an LED in the **States bar** under the
header. States are operator-editable config — the Config-page **States** root, which is
the ``networks/`` directory on disk — so the operator can add or retune them without
touching the code (P8). Two *kinds* of state-source file live side by side under that
root, both feeding the same bar:

  - **ping** (e.g. ``networks.yaml``) — each entry pings an off-fleet host: reachable →
    green, no reply → red, not-yet-checked → gray. Parsed by ``core/networks.py`` (kept
    as the ping model); this module wraps each link as an :class:`Indicator`.
  - **cmd**  (e.g. ``stateA.yaml``)  — each entry runs a command on the **base station**
    and maps its **exit code** to a named color via a ``return_colors`` ({code: color})
    map (falling back to ``default_color``).

This module is **pure**: parsing / validation / classification are a function of
``(dict|dir) -> model`` and unit-tested with no network or subprocess
(``tests/test_states.py``). The actual pinging / command execution lives in the I/O
shell ``core/state_monitor.py``.

Trust model: a cmd state's ``cmd`` runs arbitrary shell on the base station — the same
deliberate config-over-code posture as local custom commands (closed LAN, audited,
**echo-only under --mock/--dry-run**, gated by ``--no-local-commands``), not an
oversight. Keys stay bare tokens; colors are validated against a small allow-list so a
typo is a line-numbered error, not a silently dark LED.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .networks import KEY_RE, networks_from_dict

# The named colors a state may resolve to → one CSS class each (``.led.c-<name>``).
# Operator-facing, so keep the set small and predictable.
STATE_COLORS = ("green", "yellow", "red", "blue", "purple", "orange", "gray")
_COLOR_ALIASES = {"grey": "gray"}
GRAY = "gray"

DEFAULT_POLL = 10.0      # seconds between cmd-state checks (heavier than a ping)
DEFAULT_TIMEOUT = 5.0    # per-command timeout (seconds)


def normalize_color(name: Any, source: str) -> str:
    """Lower-case + alias a color name, validating it against :data:`STATE_COLORS`."""
    c = _COLOR_ALIASES.get(str(name).strip().lower(), str(name).strip().lower())
    if c not in STATE_COLORS:
        raise ValueError(
            f"{source}: unknown color {name!r}; allowed: {', '.join(STATE_COLORS)}")
    return c


@dataclass(frozen=True)
class Indicator:
    """A unified, kind-tagged status descriptor the monitor evaluates into one LED.

    ``kind`` ∈ {``ping``, ``cmd``}; the kind-specific fields are populated accordingly.
    Frozen + operator-authored, so every field reaches the UI via ``textContent`` (keep
    the XSS discipline)."""
    key: str
    label: str
    kind: str
    hint: str = ""
    timeout: float = DEFAULT_TIMEOUT
    # ping only:
    host: Optional[str] = None
    # cmd only:
    cmd: Optional[str] = None
    return_colors: Optional[Dict[int, str]] = None
    default_color: str = GRAY

    def color_for_code(self, code: int) -> str:
        """cmd: map an exit code → its color (``return_colors`` then ``default_color``)."""
        return (self.return_colors or {}).get(code, self.default_color)


# --- cmd-state file (the stateA.yaml shape) ----------------------------------
def _cmd_indicator_from_dict(d: Any, source: str, file_timeout: float) -> Indicator:
    if not isinstance(d, dict):
        raise ValueError(f"{source}: each probe must be a mapping")
    key = str(d.get("key") or "").strip()
    if not KEY_RE.match(key):
        raise ValueError(f"{source}: state key {key!r} must match {KEY_RE.pattern}")
    cmd = d.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError(f"{source}: state {key!r}: 'cmd' must be a non-empty string")
    where = f"{source}: state {key!r}"
    rc_raw = d.get("return_colors") or {}
    if not isinstance(rc_raw, dict):
        raise ValueError(f"{where}: 'return_colors' must be a mapping of exit-code → color")
    return_colors: Dict[int, str] = {}
    for code, color in rc_raw.items():
        try:
            ci = int(code)
        except (TypeError, ValueError):
            raise ValueError(f"{where}: return code {code!r} is not an integer")
        return_colors[ci] = normalize_color(color, where)
    has_default = "default_color" in d
    default_color = normalize_color(d.get("default_color", GRAY), where)
    if not return_colors and not has_default:
        raise ValueError(f"{where}: define at least one 'return_colors' entry "
                         "or a 'default_color'")
    try:
        timeout = float(d.get("timeout", file_timeout))
    except (TypeError, ValueError):
        raise ValueError(f"{where}: timeout must be a number")
    if timeout <= 0:
        raise ValueError(f"{where}: timeout must be > 0")
    return Indicator(key=key, label=str(d.get("label") or key), kind="cmd",
                     hint=str(d.get("hint") or ""), timeout=timeout, cmd=cmd,
                     return_colors=return_colors, default_color=default_color)


def cmd_states_from_dict(raw: Dict[str, Any], source: str = "<dict>"
                         ) -> Tuple[List[Indicator], float]:
    """Parse + validate a cmd-state file → ``(indicators, poll_interval)``. Raises
    ValueError. Accepts the wrapped (``{states: {...}}``) or bare block, like
    ``fleet.fleet_from_dict``."""
    block = raw.get("states", raw) if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        raise ValueError(f"{source}: 'states' must be a mapping")
    probes_raw = block.get("probes", []) or []
    if not isinstance(probes_raw, list):
        raise ValueError(f"{source}: 'states.probes' must be a list")
    try:
        poll = float(block.get("poll_interval", DEFAULT_POLL))
        file_timeout = float(block.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        raise ValueError(f"{source}: poll_interval/timeout must be numbers")
    if poll <= 0:
        raise ValueError(f"{source}: poll_interval must be > 0")
    if file_timeout <= 0:
        raise ValueError(f"{source}: timeout must be > 0")
    inds: List[Indicator] = []
    seen = set()
    for d in probes_raw:
        ind = _cmd_indicator_from_dict(d, source, file_timeout)
        if ind.key in seen:
            raise ValueError(f"{source}: duplicate state key {ind.key!r}")
        seen.add(ind.key)
        inds.append(ind)
    return inds, poll


# --- ping-state file (the networks.yaml shape) -------------------------------
def _ping_states_from_dict(raw: Dict[str, Any], source: str
                           ) -> Tuple[List[Indicator], float]:
    """Parse a ping-state file (the ``networks.yaml`` shape) via the kept ping model →
    ``(indicators, poll_interval)``."""
    nets = networks_from_dict(raw, source=source)
    inds = [Indicator(key=l.key, label=l.label, kind="ping", hint=l.hint,
                      host=l.host, timeout=nets.ping_timeout)
            for l in nets.links]
    return inds, nets.poll_interval


# --- classification ----------------------------------------------------------
def _is_cmd_file(data: Any) -> bool:
    return isinstance(data, dict) and ("probes" in data or
                                       (isinstance(data.get("states"), dict) and
                                        "probes" in data["states"]))


def _is_ping_file(data: Any) -> bool:
    return isinstance(data, dict) and ("links" in data or
                                       (isinstance(data.get("networks"), dict) and
                                        "links" in data["networks"]))


def state_file_from_dict(data: Dict[str, Any], source: str = "<dict>"
                         ) -> Tuple[List[Indicator], float]:
    """Classify + parse one state-source file by its shape → ``(indicators, poll)``.

    A ``states``/``probes`` block → cmd; a ``networks``/``links`` block → ping. Raises
    ValueError if it matches neither (so the Config page reports it on save)."""
    if _is_cmd_file(data):
        return cmd_states_from_dict(data, source=source)
    if _is_ping_file(data):
        return _ping_states_from_dict(data, source=source)
    raise ValueError(
        f"{source}: unrecognized state file — expected a 'networks'/'links' (ping) "
        "block or a 'states'/'probes' (cmd) block")


class StateRegistry:
    """All state sources under the states directory → one ordered indicator list.

    Loaded from a directory: every ``*.yaml`` is one source file, classified ping/cmd
    and flattened into ``self.indicators`` (file name order, then in-file order). The
    feature is opt-in by shipping files — an empty/absent directory yields no LEDs.

    Reloaded **in place** (``reload``) like ``Fleet`` / ``Networks`` so the monitor —
    which holds this same reference — picks up Config-page edits with no restart (P8). A
    single malformed file is skipped (the rest of the bar survives); the Config page is
    what validates a file *before* it is ever written, so this is belt-and-suspenders.
    """

    def __init__(self, directory: str):
        self.dir = directory
        self.indicators: List[Indicator] = []
        self.poll_interval: float = DEFAULT_POLL
        self.reload()

    def _yaml_files(self) -> List[str]:
        if not os.path.isdir(self.dir):
            return []
        return sorted(
            n for n in os.listdir(self.dir)
            if not n.startswith(".") and n.lower().endswith((".yaml", ".yml"))
            and os.path.isfile(os.path.join(self.dir, n)))

    def reload(self) -> "StateRegistry":
        inds: List[Indicator] = []
        polls: List[float] = []
        seen = set()
        for name in self._yaml_files():
            try:
                with open(os.path.join(self.dir, name), "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                file_inds, poll = state_file_from_dict(data, source=name)
            except (OSError, ValueError, yaml.YAMLError):
                continue                      # safe degradation: skip a broken file
            polls.append(poll)
            for ind in file_inds:
                if ind.key in seen:           # first file wins on a cross-file key clash
                    continue
                seen.add(ind.key)
                inds.append(ind)
        self.indicators = inds
        # the bar polls at the most-frequent source's cadence (floored at 1s).
        self.poll_interval = max(1.0, min(polls)) if polls else DEFAULT_POLL
        return self
