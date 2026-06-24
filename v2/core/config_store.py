"""
Config-page backend for ccflet (D8 — config over code).

The operator who runs ccflet can't touch the source, so the logic they tune lives in
editable config files; this module is the safe read / validate / write / revert layer
the **Config** page (``web/templates/config.html``) drives. It mirrors
``core/docs.py``'s path-safety discipline, but is **read-write** over a small set of
registered roots, and it **validates a submitted file into the real model before it is
ever persisted** — a non-coder gets a line-numbered error instead of a broken fleet.

Roots (the editable logic):
  - ``fleet``    → ``fleet/`` (``fleet.yaml``)              → reload scope ``fleet``
  - ``profiles`` → ``profiles/`` (``roleA.yaml``/``roleB.yaml``) → reload scope ``profiles``
  - ``commands`` → ``commands/`` (``commands_{host,roleA,roleB}.yaml`` + ``*.sh``) → scope ``commands``
  - ``states``   → ``networks/`` (``networks.yaml`` ping + ``stateA.yaml`` cmd) → scope ``states``
  - ``gates``    → ``gates/`` (``gate*.yaml`` — one health gate per file)  → reload scope ``gates``
  - ``logs``     → ``logs/`` (``logs.yaml`` — base-station log windows)  → reload scope ``logs``

The ``states`` root holds two file *kinds* (ping links + command-driven states); they
are validated by **shape** (``core/states.state_file_from_dict``), not by a fixed kind.
The ``gates`` root holds one gate per file, validated by ``core/gates_config`` (P8).

Safety: every read/write is path-resolved under its root (no traversal, no dotfiles,
extension allow-list). Writes validate first, snapshot the prior file to ``<root>/.bak/``,
then write atomically (temp + ``os.replace``). ``--dry-run`` blocks writes.

Pure-ish: behavior is a function of the roots + the files on disk, so it is unit-tested
against a tmp dir with no network (``tests/test_config_store.py``).
"""

import os
import shutil
import time

import yaml

from . import fleet as F
from . import profiles as P

BAK_DIR = ".bak"
KIND_FLEET = "fleet"
KIND_PROFILE = "profile"
KIND_COMMANDS = "commands"
KIND_STATES = "states"      # ping links + cmd states (validated by shape)
KIND_GATES = "gates"        # one health gate per file (gates_config)
KIND_LOGS = "logs"          # base-station log windows (the Logs view)
KIND_SCRIPT = "script"


class ConfigRoot:
    """One editable directory: its key, on-disk path, allowed extensions, the default
    file ``kind`` (for validation) and the hot-reload ``scope`` it maps to."""

    def __init__(self, key, label, path, exts, kind, scope):
        self.key = key
        self.label = label
        self.path = path
        self.exts = tuple(e.lower() for e in exts)
        self.kind = kind          # default kind for non-.sh files
        self.scope = scope        # CCFletApp.reload_config scope

    def kind_of(self, name):
        return KIND_SCRIPT if name.lower().endswith(".sh") else self.kind


def default_roots(fleet_path, profiles_dir, commands_dir=None, states_dir=None,
                  logs_dir=None, gates_dir=None):
    """Build the standard root set from the app's config paths."""
    roots = [
        ConfigRoot("fleet", "Fleet inventory",
                   os.path.dirname(os.path.abspath(fleet_path)),
                   (".yaml", ".yml"), KIND_FLEET, "fleet"),
        ConfigRoot("profiles", "Role profiles", profiles_dir,
                   (".yaml", ".yml"), KIND_PROFILE, "profiles"),
    ]
    if commands_dir:
        roots.append(ConfigRoot("commands", "Commands", commands_dir,
                                 (".yaml", ".yml", ".sh"), KIND_COMMANDS, "commands"))
    if states_dir:
        roots.append(ConfigRoot("states", "States", states_dir,
                                 (".yaml", ".yml"), KIND_STATES, "states"))
    if gates_dir:
        roots.append(ConfigRoot("gates", "Gates", gates_dir,
                                 (".yaml", ".yml"), KIND_GATES, "gates"))
    if logs_dir:
        roots.append(ConfigRoot("logs", "Logs", logs_dir,
                                 (".yaml", ".yml"), KIND_LOGS, "logs"))
    return roots


# --- validation (pure; reuses the real model loaders) ------------------------
def validate_text(kind, text, name=None):
    """Validate a submitted config file *by parsing it into the real model*.

    ``name`` is the file's basename — used for the split command catalog so a file is
    judged with the same per-file defaults it loads with (``commands_host.yaml`` → local).

    Returns ``{"ok": True}`` or ``{"ok": False, "error": str, "line": int|None}``.
    Never raises — a non-coder must always get a message, not a stack trace.
    """
    if kind == KIND_SCRIPT:
        if not text.strip():
            return {"ok": False, "error": "script is empty"}
        return {"ok": True}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = (mark.line + 1) if mark is not None else None
        problem = getattr(e, "problem", None) or "could not parse YAML"
        return {"ok": False, "error": f"YAML: {problem}", "line": line}
    data = data or {}
    if not isinstance(data, dict):
        return {"ok": False, "error": "top level must be a mapping (key: value)"}
    try:
        if kind == KIND_FLEET:
            F.fleet_from_dict(data, source="fleet")
        elif kind == KIND_PROFILE:
            P.profile_from_dict(data)
        elif kind == KIND_COMMANDS:
            from . import commands as C        # lazy: avoid an import cycle
            C.commands_from_dict(data, defaults=C.file_defaults(name or ""))
        elif kind == KIND_STATES:
            from . import states as S          # lazy: avoid an import cycle
            S.state_file_from_dict(data, source=name or "states")  # ping or cmd, by shape
        elif kind == KIND_GATES:
            from . import gates_config as GC   # lazy: avoid an import cycle
            GC.gate_file_from_dict(data, source=name or "gates")
        elif kind == KIND_LOGS:
            from . import logs as L            # lazy: avoid an import cycle
            L.logs_from_dict(data, source=name or "logs")
        else:
            return {"ok": False, "error": f"unknown config kind {kind!r}"}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


class ConfigStore:
    """Path-safe read / validate / write / revert over the registered roots."""

    def __init__(self, roots, dry_run=False):
        self._roots = {r.key: r for r in roots}
        self.dry_run = dry_run

    # ---- lookup ----------------------------------------------------------
    def root(self, key):
        return self._roots.get(key)

    def scope_of(self, key):
        r = self.root(key)
        return r.scope if r else None

    def _resolve(self, root, relpath, must_exist):
        """Resolve ``relpath`` to an absolute file under ``root`` or return None.

        Rejects absolute paths, traversal, any dot-prefixed component (hides
        ``.bak`` and dotfiles), and extensions outside the root's allow-list.
        """
        if not relpath:
            return None
        parts = relpath.replace("\\", "/").split("/")
        if any(p in ("", "..", ".") or p.startswith(".") for p in parts):
            return None
        root_real = os.path.realpath(root.path)
        target = os.path.realpath(os.path.join(root_real, relpath))
        if target != root_real and not target.startswith(root_real + os.sep):
            return None
        if not target.lower().endswith(root.exts):
            return None
        if must_exist and not os.path.isfile(target):
            return None
        return target

    # ---- read ------------------------------------------------------------
    def list_tree(self):
        """A flat tree of editable files per root (read fresh → reflects add/remove)."""
        out = []
        for r in self._roots.values():
            files = []
            if os.path.isdir(r.path):
                for name in sorted(os.listdir(r.path)):
                    if name.startswith("."):
                        continue
                    if not name.lower().endswith(r.exts):
                        continue
                    abs_p = os.path.join(r.path, name)
                    if not os.path.isfile(abs_p):
                        continue
                    files.append({"name": name, "path": name,
                                  "kind": r.kind_of(name),
                                  "mtime": os.path.getmtime(abs_p)})
            out.append({"key": r.key, "label": r.label, "scope": r.scope,
                        "files": files})
        return out

    def read_file(self, root_key, relpath):
        r = self.root(root_key)
        if not r:
            return None
        target = self._resolve(r, relpath, must_exist=True)
        if not target:
            return None
        try:
            with open(target, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return None
        return {"root": r.key, "path": os.path.relpath(target, r.path),
                "kind": r.kind_of(os.path.basename(target)), "text": text,
                "mtime": os.path.getmtime(target),
                "has_backup": self._newest_backup(r, target) is not None}

    # ---- validate --------------------------------------------------------
    def validate(self, root_key, relpath, text):
        r = self.root(root_key)
        if not r:
            return {"ok": False, "error": "unknown config root"}
        # resolve the path the same way a write would, so "valid" never lies about a
        # file that write_file would later refuse (wrong root/ext/traversal).
        if self._resolve(r, relpath, must_exist=False) is None:
            return {"ok": False, "error": "invalid path (outside root or disallowed extension)"}
        return validate_text(r.kind_of(relpath), text, name=os.path.basename(relpath))

    # ---- write / revert --------------------------------------------------
    def write_file(self, root_key, relpath, text):
        """Validate → back up the prior file → atomic write. Never persists invalid."""
        r = self.root(root_key)
        if not r:
            return {"ok": False, "error": "unknown config root"}
        v = self.validate(root_key, relpath, text)
        if not v.get("ok"):
            return v
        target = self._resolve(r, relpath, must_exist=False)
        if not target:
            return {"ok": False, "error": "invalid path"}
        rel = os.path.relpath(target, r.path)
        if self.dry_run:
            return {"ok": True, "path": rel, "dry_run": True,
                    "note": "dry-run: validated, not written"}
        try:
            backup = self._backup(r, target) if os.path.isfile(target) else None
            self._atomic_write(target, text)
        except OSError as e:
            return {"ok": False, "error": f"write failed: {e}"}
        return {"ok": True, "path": rel, "backup": backup,
                "mtime": os.path.getmtime(target)}

    def revert(self, root_key, relpath):
        """Restore the newest backup (snapshotting the current file first)."""
        r = self.root(root_key)
        if not r:
            return {"ok": False, "error": "unknown config root"}
        target = self._resolve(r, relpath, must_exist=False)
        if not target:
            return {"ok": False, "error": "invalid path"}
        bak = self._newest_backup(r, target)
        if not bak:
            return {"ok": False, "error": "no backup to revert to"}
        rel = os.path.relpath(target, r.path)
        try:
            with open(bak, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            return {"ok": False, "error": f"revert failed: {e}"}
        # validate the backup before restoring it — never persist invalid, even on a
        # revert (a backup could predate a schema change, or be hand-edited).
        v = validate_text(r.kind_of(os.path.basename(target)), text,
                          name=os.path.basename(target))
        if not v.get("ok"):
            return {"ok": False, "error": f"backup is invalid, not reverting: {v.get('error')}",
                    "line": v.get("line")}
        if self.dry_run:
            return {"ok": True, "path": rel, "text": text, "dry_run": True}
        try:
            if os.path.isfile(target):
                self._backup(r, target)
            self._atomic_write(target, text)
        except OSError as e:
            return {"ok": False, "error": f"revert failed: {e}"}
        return {"ok": True, "path": rel, "text": text,
                "mtime": os.path.getmtime(target)}

    # ---- backups ---------------------------------------------------------
    def _backup(self, root, target):
        bak_dir = os.path.join(root.path, BAK_DIR)
        os.makedirs(bak_dir, exist_ok=True)
        base = os.path.basename(target)
        ts = time.strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(bak_dir, f"{base}.{ts}")
        i = 0
        while os.path.exists(dest):           # avoid clobber within one second
            i += 1
            dest = os.path.join(bak_dir, f"{base}.{ts}.{i}")
        shutil.copy2(target, dest)
        return os.path.relpath(dest, root.path)

    def _newest_backup(self, root, target):
        bak_dir = os.path.join(root.path, BAK_DIR)
        if not os.path.isdir(bak_dir):
            return None
        base = os.path.basename(target)
        cands = [os.path.join(bak_dir, f) for f in os.listdir(bak_dir)
                 if f.startswith(base + ".")]
        cands = [c for c in cands if os.path.isfile(c)]
        return max(cands, key=os.path.getmtime) if cands else None

    @staticmethod
    def _atomic_write(target, text):
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
