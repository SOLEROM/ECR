"""
Operator command catalog for ccflet (D8 — config over code).

The ``commands/`` folder holds the operator-editable catalog of *extra* triggerable
commands — each entry becomes a button in the UI. **Where a command runs is decided by
which file it lives in**, so the operator can see at a glance what runs locally vs
remotely (and never has to spell out ``on:``/``role:``):

  - ``commands_host.yaml``  → runs **locally** on the base station (``on: local``)
  - ``commands_roleA.yaml`` → runs on the **roleA** host over SSH (``on: remote, role: roleA``)
  - ``commands_roleB.yaml`` → runs on the **roleB** host over SSH (``on: remote, role: roleB``)
  - ``commands.yaml``       → legacy single file (no implied defaults — kept for back-compat)

Each file's implied ``on``/``role``/``scope`` are *defaults*; an entry may still set
them explicitly. It is the configurable sibling of the per-role action profiles
(``core/profiles.py``): the same ``{param}`` substitution, but operator-authored and
surfaced as buttons with no code change.

This module is **pure** — parsing/validation are a function of (dict|file) → model and
unit-tested with no network (``tests/test_commands.py``). Execution lives in
``orchestrator.run_custom`` (remote) / ``core/local_exec.py`` (local).
"""

import os
import re
from dataclasses import dataclass, replace
from typing import Dict, List, Optional

import yaml

ON_TARGETS = ("remote", "local")
SCOPES = ("node", "fleet")
ROLES = ("roleA", "roleB")
# command names reach the UI + audit, never a shell; keep them tidy tokens.
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# The catalog is split by *where a command runs* — one file per target, so the
# operator can tell local vs remote apart at a glance and need not write `on:`/`role:`.
# Each tuple is (filename, per-file field defaults). Order = load order.
CATALOG_FILES = (
    ("commands_host.yaml", {"on": "local", "scope": "fleet"}),
    ("commands_roleA.yaml", {"on": "remote", "role": "roleA"}),
    ("commands_roleB.yaml", {"on": "remote", "role": "roleB"}),
    ("commands.yaml", {}),   # legacy single file — no implied defaults (back-compat)
)
_FILE_DEFAULTS = {fname: defaults for fname, defaults in CATALOG_FILES}


def file_defaults(filename: str) -> dict:
    """The implied ``on``/``role``/``scope`` defaults for a catalog filename.

    Used by the loader and by ``config_store`` validation so the Config-page "Check"
    judges a file exactly as it will be loaded (e.g. a role-less command in
    ``commands_host.yaml`` is a *local* command, not a malformed remote one).
    """
    return dict(_FILE_DEFAULTS.get(os.path.basename(filename), {}))


@dataclass(frozen=True)
class Command:
    name: str
    label: str
    group: str = "Commands"
    on: str = "remote"              # remote | local
    role: str = "roleA"            # remote only
    scope: str = "node"            # node | fleet
    run: str = ""                  # inline command (xor script)
    script: Optional[str] = None   # bare filename under commands/
    timeout: int = 60
    danger: bool = False

    def to_meta(self) -> dict:
        """Metadata the UI needs to render a button — never the command body."""
        return {"name": self.name, "label": self.label, "group": self.group,
                "on": self.on, "role": self.role, "scope": self.scope,
                "danger": self.danger, "has_script": bool(self.script)}


def _command_from_dict(name, d, default_timeout: int, defaults=None) -> Command:
    defaults = defaults or {}
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f"command name {name!r} must match {NAME_RE.pattern}")
    if not isinstance(d, dict):
        raise ValueError(f"command {name!r}: definition must be a mapping")
    # YAML 1.1 parses an *unquoted* `on:` key as the boolean True (a classic
    # footgun). Accept both so the natural `on: local` works without the operator
    # having to quote the key — silently defaulting a "local" command to "remote"
    # would be dangerous. When the command lives in a target-specific file
    # (commands_host/roleA/roleB) the file's default supplies `on`/`role` with no key at all.
    on = d.get("on", d.get(True, defaults.get("on", "remote")))
    if on not in ON_TARGETS:
        raise ValueError(f"command {name!r}: 'on' must be one of {ON_TARGETS}")
    scope = d.get("scope", defaults.get("scope", "node"))
    if scope not in SCOPES:
        raise ValueError(f"command {name!r}: 'scope' must be one of {SCOPES}")
    role = d.get("role", defaults.get("role", "roleA"))
    if on == "remote" and role not in ROLES:
        raise ValueError(f"command {name!r}: 'role' must be one of {ROLES}")
    run = (d.get("run") or "").strip()
    script = d.get("script")
    if bool(run) == bool(script):
        raise ValueError(f"command {name!r}: set exactly one of 'run' or 'script'")
    if script is not None:
        script = str(script)
        if "/" in script or "\\" in script or script.startswith("."):
            raise ValueError(
                f"command {name!r}: 'script' must be a bare filename under commands/")
    return Command(
        name=name,
        label=str(d.get("label") or name),
        group=str(d.get("group") or "Commands"),
        on=on, role=role, scope=scope,
        run=run, script=script,
        timeout=int(d.get("timeout", default_timeout)),
        danger=bool(d.get("danger", False)),
    )


def commands_from_dict(data, defaults=None) -> Dict[str, Command]:
    """Parse + validate a catalog dict → ``{name: Command}``. Raises ValueError.

    ``defaults`` carries the per-file implied fields (``on``/``role``/``scope``) for the
    split catalog files; ``None`` means "no implied defaults" (legacy ``commands.yaml``).
    """
    data = data or {}
    if not isinstance(data, dict):
        raise ValueError("commands file: top level must be a mapping")
    settings = data.get("settings", {}) or {}
    default_timeout = int(settings.get("default_timeout", 60))
    out: Dict[str, Command] = {}
    for name, d in (data.get("commands", {}) or {}).items():
        out[name] = _command_from_dict(name, d, default_timeout, defaults)
    return out


class CommandCatalog:
    """Loads + holds the operator command catalog; reloads on a Config-page save.

    Reads every present catalog file in the commands dir (``commands_host/roleA/roleB.yaml``
    plus a legacy ``commands.yaml``) and merges them into one ``{name: Command}`` map,
    applying each file's implied target defaults. ``path`` may be the commands directory
    or any file inside it (its directory is used)."""

    def __init__(self, path: str):
        self.path = path
        abs_p = os.path.abspath(path)
        self.dir = abs_p if os.path.isdir(abs_p) else os.path.dirname(abs_p)
        self._commands: Dict[str, Command] = {}
        self.load()

    def load(self):
        # build into a local dict and assign at the end, so a bad edit (e.g. a name
        # colliding across files) leaves the previously-loaded catalog intact.
        out: Dict[str, Command] = {}
        origin: Dict[str, str] = {}
        for fname, defaults in CATALOG_FILES:
            fpath = os.path.join(self.dir, fname)
            if not os.path.isfile(fpath):
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for name, cmd in commands_from_dict(data, defaults=defaults).items():
                if name in out:
                    raise ValueError(
                        f"duplicate command {name!r} in {fname} "
                        f"(already defined in {origin[name]})")
                out[name] = cmd
                origin[name] = fname
        self._commands = out

    def reload(self):
        self.load()

    def get(self, name: str) -> Optional[Command]:
        return self._commands.get(name)

    def all(self) -> List[Command]:
        return list(self._commands.values())

    def metas(self) -> List[dict]:
        return [c.to_meta() for c in self._commands.values()]

    def script_path(self, cmd: Command) -> Optional[str]:
        """Path-safe absolute path of a command's script, or None."""
        if not cmd.script:
            return None
        target = os.path.realpath(os.path.join(self.dir, cmd.script))
        root = os.path.realpath(self.dir)
        if target != root and not target.startswith(root + os.sep):
            return None
        return target if os.path.isfile(target) else None
