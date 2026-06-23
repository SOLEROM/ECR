"""
Log-windows model for ccflet (config over code) — the **Logs** view.

``logs/logs.yaml`` is the operator-editable list of **log windows** shown on the
dashboard's third view ("Logs"): one live ``tail -F`` pane per entry, each watching a
file **on the base station** (the machine running ccFleet). It is the read-only,
many-panes-at-once counterpart of the per-node "Live logs" tab — but for the control
station's own processes (a daemon's log, a service's stdout, the system log, …) rather
than a fleet node.

Each window names the **process** being watched and the **path** of its log file; the
operator adds/edits/removes windows from the Config page (the ``logs`` root), validated
→ hot-reloaded → audited like any other config (P8). The windows are saved into the
session ZIP as artifacts on export (``artifacts/logs/<key>.log``), so a run always
carries the logs the operator chose to watch.

Like ``core/networks.py`` / ``core/states.py`` this module is **pure**: parsing and
validation are a function of ``(dict|dir) -> model`` and unit-tested with no filesystem
tailing (``tests/test_logs.py``). The actual local tailing + the artifact snapshot live
in the I/O shell ``core/log_stream.py``.

Trust model: ``path`` is operator-authored config and is tailed via ``tail -F`` passed
as an **argv list** (never a shell), so there is no injection surface; it is still the
base-station-local, config-over-code posture (closed LAN, audited) — echo-only under
``--mock``/``--dry-run`` and gated by ``--no-local-commands`` (see ``core/log_stream.py``).
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List

import yaml

from .networks import KEY_RE   # window keys reach the UI + audit — keep them bare tokens

DEFAULT_LINES = 200      # how many trailing lines a window seeds with (tail -n)
MAX_LINES = 5000         # sanity cap so a typo can't request a giant backscroll


@dataclass(frozen=True)
class LogWindow:
    """One base-station log file to tail → one pane in the Logs view.

    Frozen + operator-authored, so every field reaches the UI via ``textContent``
    (keep the XSS discipline)."""
    key: str
    label: str
    path: str
    process: str = ""
    lines: int = DEFAULT_LINES
    hint: str = ""

    def to_meta(self) -> Dict[str, Any]:
        """What the UI needs to render a window + its header (all operator-authored,
        rendered client-side with ``textContent``)."""
        return {"key": self.key, "label": self.label, "path": self.path,
                "process": self.process, "lines": self.lines, "hint": self.hint}


def _window_from_dict(d: Any, source: str, file_lines: int) -> LogWindow:
    if not isinstance(d, dict):
        raise ValueError(f"{source}: each log window must be a mapping")
    key = str(d.get("key") or "").strip()
    if not KEY_RE.match(key):
        raise ValueError(f"{source}: log key {key!r} must match {KEY_RE.pattern}")
    where = f"{source}: log {key!r}"
    path = d.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"{where}: 'path' must be a non-empty string")
    path = path.strip()
    if "\x00" in path or "\n" in path:
        raise ValueError(f"{where}: 'path' must be a single file path")
    try:
        lines = int(d.get("lines", file_lines))
    except (TypeError, ValueError):
        raise ValueError(f"{where}: 'lines' must be an integer")
    if lines <= 0:
        raise ValueError(f"{where}: 'lines' must be > 0")
    if lines > MAX_LINES:
        lines = MAX_LINES
    process = str(d.get("process") or "").strip()
    label = str(d.get("label") or process or key)
    return LogWindow(key=key, label=label, path=path, process=process,
                     lines=lines, hint=str(d.get("hint") or ""))


def logs_from_dict(raw: Dict[str, Any], source: str = "<dict>") -> List[LogWindow]:
    """Parse + validate a logs config dict → ``[LogWindow]``. Raises ValueError.

    Accepts the wrapped shape (``{logs: {...}}``, the file form) or the bare block —
    the same forgiving convention as ``fleet.fleet_from_dict`` / ``networks_from_dict``.
    A file-level ``default_lines`` sets the tail depth for windows that don't override it.
    """
    block = raw.get("logs", raw) if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        raise ValueError(f"{source}: 'logs' must be a mapping")
    try:
        file_lines = int(block.get("default_lines", DEFAULT_LINES))
    except (TypeError, ValueError):
        raise ValueError(f"{source}: 'default_lines' must be an integer")
    if file_lines <= 0:
        raise ValueError(f"{source}: 'default_lines' must be > 0")
    windows_raw = block.get("windows", []) or []
    if not isinstance(windows_raw, list):
        raise ValueError(f"{source}: 'logs.windows' must be a list")
    windows: List[LogWindow] = []
    seen = set()
    for d in windows_raw:
        win = _window_from_dict(d, source, file_lines)
        if win.key in seen:
            raise ValueError(f"{source}: duplicate log key {win.key!r}")
        seen.add(win.key)
        windows.append(win)
    return windows


class LogsRegistry:
    """All log-window source files under the logs directory → one ordered window list.

    Loaded from a directory: every ``*.yaml`` is one source file, flattened into
    ``self.windows`` (file-name order, then in-file order). The feature is opt-in by
    shipping files — an empty/absent directory yields no windows (the Logs view shows a
    "nothing configured" hint).

    Reloaded **in place** (``reload``) like ``Fleet`` / ``StateRegistry`` so the live
    streamer + the dashboard pick up Config-page edits with no restart (P8). A single
    malformed file is skipped (the rest survive); the Config page validates a file
    *before* it is ever written, so this is belt-and-suspenders.
    """

    def __init__(self, directory: str):
        self.dir = directory
        self.windows: List[LogWindow] = []
        self.reload()

    def _yaml_files(self) -> List[str]:
        if not os.path.isdir(self.dir):
            return []
        return sorted(
            n for n in os.listdir(self.dir)
            if not n.startswith(".") and n.lower().endswith((".yaml", ".yml"))
            and os.path.isfile(os.path.join(self.dir, n)))

    def reload(self) -> "LogsRegistry":
        wins: List[LogWindow] = []
        seen = set()
        for name in self._yaml_files():
            try:
                with open(os.path.join(self.dir, name), "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                file_wins = logs_from_dict(data, source=name)
            except (OSError, ValueError, yaml.YAMLError):
                continue                      # safe degradation: skip a broken file
            for win in file_wins:
                if win.key in seen:           # first file wins on a cross-file key clash
                    continue
                seen.add(win.key)
                wins.append(win)
        self.windows = wins
        return self

    def get(self, key: str):
        for w in self.windows:
            if w.key == key:
                return w
        return None

    def metas(self) -> List[Dict[str, Any]]:
        return [w.to_meta() for w in self.windows]
