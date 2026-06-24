"""Unit tests for core.config_profiles (the switchable editable-YAML sets, P8).

Pure path/file logic — exercised against a temp app root, no network. Every profile —
``default`` included — is a subdir of ``yamls/<name>/``; covers uniform resolution,
discovery, name safety, persistence of the active choice, and scaffolding a new profile
as an independent clone.

(The template keeps command ``*.sh`` scripts inside the ``commands/`` root — there is no
separate ``command_scripts/`` root — so a profile is the six config roots below.)
"""

import os

import pytest

from core.config_profiles import ConfigProfiles, valid_name, DEFAULT_PROFILE

_FLEET_YAML = (
    "fleet:\n  name: t\n"
    "  nodes:\n    - {name: n1, id: 1, host: 10.0.0.1, subnet: 10.0.0}\n"
)


def _mk_profile(root, name):
    """A minimal config-root layout under ``yamls/<name>/``; returns its base dir."""
    base = root / "yamls" / name
    (base / "fleet").mkdir(parents=True)
    (base / "fleet" / "fleet.yaml").write_text(_FLEET_YAML)
    for d in ("profiles", "commands", "networks", "gates", "logs"):
        (base / d).mkdir()
    (base / "profiles" / "roleA.yaml").write_text("connection: {host: '{HOST}'}\nactions: {}\n")
    (base / "gates" / "gateA.yaml").write_text("key: A\nkind: reach\nlabel: reach\n")
    (base / "commands" / "x.sh").write_text("echo hi\n")        # a command script lives in commands/
    return base


@pytest.fixture
def reg(tmp_path):
    _mk_profile(tmp_path, DEFAULT_PROFILE)
    return ConfigProfiles(str(tmp_path)), tmp_path


# ---- default profile resolves under yamls/default/ --------------------------
def test_default_resolve_under_yamls(reg, tmp_path):
    cp, _ = reg
    r = cp.resolve(DEFAULT_PROFILE)
    base = tmp_path / "yamls" / "default"
    assert r["fleet_path"] == str(base / "fleet" / "fleet.yaml")
    assert r["profiles_dir"] == str(base / "profiles")
    assert r["commands_dir"] == str(base / "commands")
    assert r["states_dir"] == str(base / "networks")           # States root is networks/ on disk
    assert r["gates_dir"] == str(base / "gates")
    assert r["logs_dir"] == str(base / "logs")


def test_default_exists_and_is_listed(reg):
    cp, _ = reg
    assert cp.exists(DEFAULT_PROFILE)
    assert cp.active == DEFAULT_PROFILE          # nothing persisted yet
    assert cp.list() == [DEFAULT_PROFILE]


# ---- name safety ------------------------------------------------------------
@pytest.mark.parametrize("name", ["sim", "sandbox-2", "ABC_123"])
def test_valid_names(name):
    assert valid_name(name)


@pytest.mark.parametrize("name", ["", "default", "..", "a/b", ".hidden", "x y", "-bad", "n" * 41])
def test_invalid_names(name):
    assert not valid_name(name)


# ---- create (scaffold a clone) ----------------------------------------------
def test_create_clones_and_resolves_under_parent(reg, tmp_path):
    cp, _ = reg
    res = cp.create("sim")
    assert res["ok"] and res["profile"] == "sim" and res["from"] == DEFAULT_PROFILE
    assert cp.exists("sim")
    assert cp.list() == [DEFAULT_PROFILE, "sim"]
    r = cp.resolve("sim")
    base = tmp_path / "yamls" / "sim"
    assert r["fleet_path"] == str(base / "fleet" / "fleet.yaml")
    assert r["gates_dir"] == str(base / "gates")
    # the editable files were copied (including the command script inside commands/)
    assert os.path.isfile(r["fleet_path"])
    assert os.path.isfile(os.path.join(r["profiles_dir"], "roleA.yaml"))
    assert os.path.isfile(os.path.join(r["gates_dir"], "gateA.yaml"))
    assert os.path.isfile(os.path.join(r["commands_dir"], "x.sh"))


def test_create_copies_only_editable_files(reg, tmp_path):
    cp, _ = reg
    # noise in the source that must NOT be copied (dotfile, backup dir, foreign ext)
    g = tmp_path / "yamls" / "default" / "gates"
    (g / "notes.txt").write_text("ignore me")
    (g / ".secret.yaml").write_text("nope")
    (g / ".bak").mkdir()
    (g / ".bak" / "gateA.yaml.old").write_text("old")
    cp.create("sim")
    dst = tmp_path / "yamls" / "sim" / "gates"
    assert sorted(os.listdir(dst)) == ["gateA.yaml"]   # only the real, editable file


def test_create_rejects_default_dup_and_bad(reg):
    cp, _ = reg
    assert not cp.create(DEFAULT_PROFILE)["ok"]
    assert not cp.create("..")["ok"]
    assert not cp.create("a/b")["ok"]
    assert cp.create("sim")["ok"]
    dup = cp.create("sim")
    assert not dup["ok"] and "exists" in dup["error"]


def test_create_from_unknown_source_fails(reg):
    cp, _ = reg
    res = cp.create("sim", from_name="ghost")
    assert not res["ok"] and "unknown source" in res["error"]


# ---- active persistence -----------------------------------------------------
def test_set_active_persists_across_instances(reg, tmp_path):
    cp, _ = reg
    cp.create("sim")
    cp.set_active("sim", persist=True)
    assert cp.active == "sim"
    again = ConfigProfiles(str(tmp_path))            # fresh instance reads the marker
    assert again.active == "sim"
    again.set_active(DEFAULT_PROFILE, persist=True)
    assert ConfigProfiles(str(tmp_path)).active == DEFAULT_PROFILE


def test_active_marker_for_missing_profile_falls_back(reg, tmp_path):
    cp, _ = reg
    (tmp_path / "yamls" / "active").write_text("ghost\n")
    assert ConfigProfiles(str(tmp_path)).active == DEFAULT_PROFILE


def test_set_active_without_persist_leaves_no_marker(reg, tmp_path):
    cp, _ = reg
    cp.create("sim")
    cp.set_active("sim", persist=False)
    assert cp.active == "sim"
    assert ConfigProfiles(str(tmp_path)).active == DEFAULT_PROFILE  # nothing on disk


def test_active_marker_not_listed_as_profile(reg, tmp_path):
    cp, _ = reg
    cp.set_active(DEFAULT_PROFILE, persist=True)     # writes yamls/active (a file, not a dir)
    assert ConfigProfiles(str(tmp_path)).list() == [DEFAULT_PROFILE]
