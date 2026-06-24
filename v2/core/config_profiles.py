"""
Config profiles for ccflet — switch the whole set of editable YAMLs at once (P8).

A *config profile* is a complete, self-contained set of the operator-editable config
roots (fleet · profiles · commands · states · gates · logs). The app reads its live
config from exactly one **active** profile at a time, so an operator can keep a real
``default`` fleet and a throwaway ``sim`` sandbox side by side and flip between them from
the Config page — hot, no restart, and with **no effect on the other profile's files**.

Layout
------
**Every** profile — including ``default`` — is a subdir of a single aggregation root
(``yamls/<name>/``) holding the same config-root subdirs. The default profile is just the
one named ``default``; there is no special-cased top-level layout::

    yamls/
      active                       # one line: the persisted active profile name
      default/
        fleet/fleet.yaml
        profiles/{roleA,roleB}.yaml
        commands/commands_{host,roleA,roleB}.yaml (+ any *.sh a command's script: names)
        networks/{networks,stateA}.yaml
        gates/gate{A..D}.yaml
        logs/logs.yaml
      sim/
        … (same subdirs)

This is the "aggregate all the YAMLs in one folder, managed by profiles" shape, applied
uniformly — so the Compiler emits the default profile into ``yamls/default/`` and an
alternate profile is a sibling clone the operator can edit in isolation.

Purity: this is path math + file copy only. It does no network I/O and is unit-tested
against a temp dir (``tests/test_config_profiles.py``).

(The template keeps a command's ``*.sh`` scripts *inside* its ``commands/`` root — there
is no separate ``command_scripts/`` root — so a profile is the six config roots below.)
"""

import os
import re
import shutil

DEFAULT_PROFILE = "default"
PARENT_DIRNAME = "yamls"
ACTIVE_FILE = "active"
# a profile name is a filesystem path component the operator can type — keep it to a safe
# slug (letters/digits/dash/underscore, leading alnum) so it can never traverse or hide.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")

# the conventional config-root subdirs inside a profile base (uniform for every profile).
# These keys mirror the per-root arguments create_app/switch_profile consume, so resolve()
# hands back exactly the paths they need.
_SUBDIRS = {
    "fleet_path": ("fleet", "fleet.yaml"),
    "profiles_dir": ("profiles",),
    "commands_dir": ("commands",),
    "states_dir": ("networks",),
    "gates_dir": ("gates",),
    "logs_dir": ("logs",),
}
# the editable files a clone carries (YAML config + the command scripts that live in
# commands/); never copy backups, dotfiles or foreign artifacts.
_COPY_EXTS = (".yaml", ".yml", ".sh")


def valid_name(name):
    """True if ``name`` is a legal *non-default* profile name (a safe slug)."""
    return bool(name) and name != DEFAULT_PROFILE and bool(NAME_RE.match(name))


class ConfigProfiles:
    """Discover, resolve, persist and scaffold the app's config profiles.

    Constructed with the app root; every profile (``default`` included) resolves under
    ``yamls/<name>/`` with the same config-root subdirs, so the default profile is just
    one more entry in the tree.
    """

    def __init__(self, app_root, parent_dirname=PARENT_DIRNAME):
        self.app_root = os.path.abspath(app_root)
        self.parent = os.path.join(self.app_root, parent_dirname)   # yamls/
        self._active = self._read_active()

    # ---- active (persisted) ---------------------------------------------
    @property
    def active(self):
        return self._active

    def _active_path(self):
        return os.path.join(self.parent, ACTIVE_FILE)

    def _read_active(self):
        """The persisted active profile, or ``default`` if absent/invalid/missing-dir."""
        try:
            with open(self._active_path(), encoding="utf-8") as f:
                name = f.read().strip()
        except OSError:
            return DEFAULT_PROFILE
        return name if self.exists(name) else DEFAULT_PROFILE

    def set_active(self, name, persist=True):
        """Set the active profile (in memory), optionally persisting it to disk so a
        restart remembers the operator's choice. Writing is best-effort + atomic."""
        self._active = name
        if persist:
            try:
                os.makedirs(self.parent, exist_ok=True)
                tmp = self._active_path() + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(name + "\n")
                os.replace(tmp, self._active_path())
            except OSError:
                pass
        return name

    # ---- discovery -------------------------------------------------------
    def base(self, name):
        """The base directory a profile's config roots live under (``yamls/<name>``)."""
        return os.path.join(self.parent, name)

    def exists(self, name):
        if not (name == DEFAULT_PROFILE or valid_name(name)):
            return False
        return os.path.isdir(self.base(name))

    def list(self):
        """``["default", *sorted alternate profiles]`` — read fresh from disk. ``default``
        is pinned first when present; the ``active`` marker file is skipped (not a dir)."""
        names = []
        if os.path.isdir(self.parent):
            for n in sorted(os.listdir(self.parent)):
                p = os.path.join(self.parent, n)
                if n.startswith(".") or not os.path.isdir(p):
                    continue
                names.append(n)
        if DEFAULT_PROFILE in names:
            names.remove(DEFAULT_PROFILE)
            names.insert(0, DEFAULT_PROFILE)
        elif not names:
            names = [DEFAULT_PROFILE]
        return names

    # ---- resolution ------------------------------------------------------
    def resolve(self, name):
        """The config-root paths for ``name`` (the same keys ``create_app`` consumes)."""
        base = self.base(name)
        return {key: os.path.join(base, *parts) for key, parts in _SUBDIRS.items()}

    # ---- scaffolding -----------------------------------------------------
    def create(self, name, from_name=None):
        """Scaffold a new profile by copying another profile's config roots (default by
        default). Copies only the editable files (``*.yaml``/``*.yml``/``*.sh``), never
        backups or dotfiles, so the new profile is an independent, editable clone."""
        if not valid_name(name):
            return {"ok": False,
                    "error": "invalid profile name (letters, digits, - or _; not 'default')"}
        if self.exists(name):
            return {"ok": False, "error": f"profile {name!r} already exists"}
        from_name = from_name or DEFAULT_PROFILE
        if not self.exists(from_name):
            return {"ok": False, "error": f"unknown source profile {from_name!r}"}
        src = self.resolve(from_name)
        dst = self.resolve(name)
        dst_base = self.base(name)
        try:
            os.makedirs(dst_base, exist_ok=True)
            self._copy_root(os.path.dirname(src["fleet_path"]),
                            os.path.dirname(dst["fleet_path"]))
            for key in ("profiles_dir", "commands_dir", "states_dir",
                        "gates_dir", "logs_dir"):
                self._copy_root(src[key], dst[key])
        except OSError as e:
            return {"ok": False, "error": f"could not create profile: {e}"}
        return {"ok": True, "profile": name, "from": from_name, "path": dst_base}

    @staticmethod
    def _copy_root(src, dst):
        if not src or not os.path.isdir(src):
            return
        os.makedirs(dst, exist_ok=True)
        for n in os.listdir(src):
            if n.startswith("."):
                continue
            sp = os.path.join(src, n)
            if not os.path.isfile(sp) or not n.lower().endswith(_COPY_EXTS):
                continue
            shutil.copy2(sp, os.path.join(dst, n))
