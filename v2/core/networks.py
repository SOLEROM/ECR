"""
Base-station connectivity model for ccflet (config over code) — the **ping** kind of
"state" (see ``core/states.py`` for the umbrella + the cmd kind).

``networks/networks.yaml`` is the operator-editable list of **off-fleet** links the
base station should keep an eye on — e.g. the gateway it sits behind, an upstream
reachability target, and any other device that matters before a run — surfaced as
status LEDs in the States bar under the header (green = reachable, red = no reply).
These are **not** fleet nodes; they are plain reachability checks the base station runs
locally (ICMP ping), so they live in their own small config rather than the fleet
inventory.

Like ``core/commands.py``, this module is **pure**: parsing/validation are a function
of ``(dict|file) -> model`` and unit-tested with no network
(``tests/test_networks.py``). ``core/states.py`` wraps each link as an ``Indicator``;
the actual pinging + polling lives in the I/O shell ``core/state_monitor.py``.
"""

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List

import yaml

# link keys reach the UI + audit, never a shell; keep them tidy tokens.
KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# host reaches a ping() subprocess argv (a list, never a shell), but keep it a bare
# host/IP token anyway — same allowlist-by-shape discipline as a fleet node host/subnet.
HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

DEFAULT_POLL = 5.0       # seconds between connectivity checks
DEFAULT_TIMEOUT = 1.0    # seconds to wait for a ping reply


@dataclass(frozen=True)
class NetLink:
    """One base-station link to watch → one top-bar LED."""
    key: str
    label: str
    host: str
    hint: str = ""

    def to_meta(self) -> Dict[str, Any]:
        """What the UI needs to render the LED + tooltip (all operator-authored,
        rendered with ``textContent`` client-side — keep the XSS discipline)."""
        return {"key": self.key, "label": self.label, "host": self.host,
                "hint": self.hint}


def _link_from_dict(d: Any, source: str) -> NetLink:
    if not isinstance(d, dict):
        raise ValueError(f"{source}: each link must be a mapping")
    key = str(d.get("key") or "").strip()
    if not KEY_RE.match(key):
        raise ValueError(f"{source}: link key {key!r} must match {KEY_RE.pattern}")
    host = str(d.get("host") or "").strip()
    if not HOST_RE.match(host):
        raise ValueError(
            f"{source}: link {key!r}: host {host!r} is not a valid host/IP token")
    return NetLink(key=key, label=str(d.get("label") or key), host=host,
                   hint=str(d.get("hint") or ""))


def networks_from_dict(raw: Dict[str, Any], source: str = "<dict>") -> "Networks":
    """Parse + validate a ``networks`` config dict → ``Networks``. Raises ValueError.

    Accepts either the wrapped shape (``{networks: {...}}``, the file form) or the
    bare block — same forgiving convention as ``fleet.fleet_from_dict``.
    """
    block = raw.get("networks", raw) if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        raise ValueError(f"{source}: 'networks' must be a mapping")
    links_raw = block.get("links", []) or []
    if not isinstance(links_raw, list):
        raise ValueError(f"{source}: 'networks.links' must be a list")
    links: List[NetLink] = []
    seen = set()
    for d in links_raw:
        link = _link_from_dict(d, source)
        if link.key in seen:
            raise ValueError(f"{source}: duplicate link key {link.key!r}")
        seen.add(link.key)
        links.append(link)
    try:
        poll = float(block.get("poll_interval", DEFAULT_POLL))
        timeout = float(block.get("ping_timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        raise ValueError(f"{source}: poll_interval/ping_timeout must be numbers")
    if poll <= 0:
        raise ValueError(f"{source}: poll_interval must be > 0")
    if timeout <= 0:
        raise ValueError(f"{source}: ping_timeout must be > 0")
    return Networks(links, poll_interval=poll, ping_timeout=timeout)


def load_networks(filepath: str) -> "Networks":
    """Load a networks config from YAML, or an empty set if the file is absent
    (the LEDs simply don't render — the feature is opt-in by shipping the file)."""
    if not os.path.isfile(filepath):
        return Networks([])
    with open(filepath, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return networks_from_dict(raw, source=filepath)


class Networks:
    """The base-station link list + poll/timeout settings.

    Reloaded **in place** (``reload_from_dict``) like ``Fleet`` so the monitor, which
    holds this same reference, picks up Config-page edits with no restart (D8).
    """

    def __init__(self, links: List[NetLink], poll_interval: float = DEFAULT_POLL,
                 ping_timeout: float = DEFAULT_TIMEOUT):
        self.links = list(links)
        self.poll_interval = poll_interval
        self.ping_timeout = ping_timeout

    def metas(self) -> List[Dict[str, Any]]:
        return [l.to_meta() for l in self.links]

    def reload_from_dict(self, raw: Dict[str, Any], source: str = "<reload>") -> "Networks":
        fresh = networks_from_dict(raw, source=source)
        self.links = fresh.links
        self.poll_interval = fresh.poll_interval
        self.ping_timeout = fresh.ping_timeout
        return self
