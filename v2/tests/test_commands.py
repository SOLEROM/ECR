"""Operator command catalog: schema parsing, validation, script resolution."""

import os
import pytest

from core.commands import commands_from_dict, CommandCatalog, file_defaults


def test_parse_remote_and_local():
    data = {"settings": {"default_timeout": 30}, "commands": {
        "df":   {"label": "Disk", "on": "remote", "role": "roleA",
                 "scope": "node", "run": "df -h"},
        "arch": {"label": "Arch", "on": "local", "scope": "fleet", "script": "a.sh"},
    }}
    cmds = commands_from_dict(data)
    assert cmds["df"].on == "remote" and cmds["df"].timeout == 30   # inherits default
    assert cmds["arch"].on == "local" and cmds["arch"].script == "a.sh"


def test_run_xor_script_required():
    with pytest.raises(ValueError, match="exactly one"):
        commands_from_dict({"commands": {"x": {"label": "x", "run": "a", "script": "b.sh"}}})
    with pytest.raises(ValueError, match="exactly one"):
        commands_from_dict({"commands": {"x": {"label": "x"}}})


def test_invalid_on_scope_role():
    with pytest.raises(ValueError, match="'on'"):
        commands_from_dict({"commands": {"x": {"on": "satellite", "run": "a"}}})
    with pytest.raises(ValueError, match="'scope'"):
        commands_from_dict({"commands": {"x": {"scope": "galaxy", "run": "a"}}})
    with pytest.raises(ValueError, match="'role'"):
        commands_from_dict({"commands": {"x": {"on": "remote", "role": "zzz", "run": "a"}}})


def test_script_must_be_bare_filename():
    with pytest.raises(ValueError, match="bare filename"):
        commands_from_dict({"commands": {"x": {"on": "local", "scope": "fleet",
                                               "script": "../escape.sh"}}})


def test_name_validated():
    with pytest.raises(ValueError, match="command name"):
        commands_from_dict({"commands": {"bad name": {"run": "a", "label": "x"}}})


def test_meta_carries_no_command_body():
    cmds = commands_from_dict({"commands": {"df": {"label": "D", "on": "remote",
                                                   "role": "roleA", "run": "df -h"}}})
    m = cmds["df"].to_meta()
    assert "run" not in m and "script" not in m
    assert m["name"] == "df" and m["on"] == "remote" and m["has_script"] is False


def test_catalog_loads_split_shipped_files():
    """The shipped catalog is split by target; the dir loads + merges all three."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cat = CommandCatalog(os.path.join(here, "commands"))
    names = [c.name for c in cat.all()]
    assert "df_data" in names and "archive_runs" in names and "roleB_uptime" in names
    # the file a command lives in decides where it runs — no `on:`/`role:` needed
    assert cat.get("df_data").on == "remote" and cat.get("df_data").role == "roleA"
    assert cat.get("roleB_uptime").on == "remote" and cat.get("roleB_uptime").role == "roleB"
    assert cat.get("archive_runs").on == "local"
    arch = cat.get("archive_runs")
    assert cat.script_path(arch) and cat.script_path(arch).endswith("archive_runs.sh")


def test_catalog_missing_dir_is_empty(tmp_path):
    cat = CommandCatalog(str(tmp_path / "nope.yaml"))   # dir is empty → no files
    assert cat.all() == []


# ---- split-by-file defaults -------------------------------------------------
def test_file_defaults_helper():
    assert file_defaults("commands_host.yaml")["on"] == "local"
    assert file_defaults("commands_roleA.yaml") == {"on": "remote", "role": "roleA"}
    assert file_defaults("commands_roleB.yaml")["role"] == "roleB"
    assert file_defaults("commands.yaml") == {}          # legacy: no implied defaults
    assert file_defaults("anything_else.yaml") == {}


def test_defaults_supply_on_and_role_without_keys():
    # a host-file command needs neither `on:` nor `role:`; the file implies them.
    cmds = commands_from_dict({"commands": {"clean": {"label": "C", "run": "rm -rf x"}}},
                              defaults={"on": "local", "scope": "fleet"})
    assert cmds["clean"].on == "local" and cmds["clean"].scope == "fleet"
    # explicit value still wins over the file default
    cmds = commands_from_dict({"commands": {"u": {"label": "U", "scope": "node",
                                                  "run": "uptime"}}},
                              defaults={"on": "remote", "role": "roleB"})
    assert cmds["u"].role == "roleB" and cmds["u"].scope == "node"


def test_split_files_merge_and_reject_duplicate_name(tmp_path):
    (tmp_path / "commands_roleA.yaml").write_text(
        "commands:\n  shared: {label: A, scope: node, run: 'uptime'}\n")
    (tmp_path / "commands_host.yaml").write_text(
        "commands:\n  base: {label: H, run: 'df -h .'}\n")
    cat = CommandCatalog(str(tmp_path))
    assert cat.get("shared").role == "roleA" and cat.get("base").on == "local"
    # a name appearing in two files is a likely operator mistake → loud, atomic fail
    (tmp_path / "commands_host.yaml").write_text(
        "commands:\n  shared: {label: H, run: 'df -h .'}\n")
    with pytest.raises(ValueError, match="duplicate command 'shared'"):
        cat.reload()
    assert cat.get("shared").role == "roleA"   # prior good catalog kept on bad reload
