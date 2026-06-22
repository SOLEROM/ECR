"""Config-page store: path safety, validation-before-write, backup + revert."""

import os
import pytest

from core.config_store import ConfigStore, default_roots, validate_text

GOOD_FLEET = """\
fleet:
  name: t
  defaults: {variant: A, algo: alpha}
  nodes:
    - {name: d1, id: 1, host: h1, subnet: 10.0.0}
"""
DUP_ID_FLEET = """\
fleet:
  nodes:
    - {name: d1, id: 1, host: h1, subnet: 10.0.0}
    - {name: d2, id: 1, host: h2, subnet: 10.0.1}
"""
BAD_YAML = "fleet:\n  a: b: c\n"
MIN_PROFILE = "name: roleA\nconnection: {host: h}\nactions: {}\n"
BAD_PROFILE = "actions:\n  x: {kind: bogus}\n"
GOOD_COMMANDS = "commands:\n  ping: {label: P, on: remote, role: roleA, run: 'echo hi'}\n"
GOOD_NETWORKS = "networks:\n  links:\n    - {key: link1, label: Gateway, host: 10.0.0.1}\n"
BAD_NETWORKS = "networks:\n  links:\n    - {key: 'bad key', host: h}\n"


def make_store(tmp_path, dry_run=False):
    fleet_dir = tmp_path / "fleet"; fleet_dir.mkdir()
    prof_dir = tmp_path / "profiles"; prof_dir.mkdir()
    cmd_dir = tmp_path / "commands"; cmd_dir.mkdir()
    (fleet_dir / "fleet.yaml").write_text(GOOD_FLEET)
    (prof_dir / "roleA.yaml").write_text(MIN_PROFILE)
    (cmd_dir / "commands.yaml").write_text(GOOD_COMMANDS)
    (cmd_dir / "archive.sh").write_text("#!/bin/sh\necho hi\n")
    roots = default_roots(str(fleet_dir / "fleet.yaml"), str(prof_dir), str(cmd_dir))
    return ConfigStore(roots, dry_run=dry_run), {
        "fleet_dir": fleet_dir, "prof_dir": prof_dir, "cmd_dir": cmd_dir}


# ---- validation -------------------------------------------------------------
def test_validate_good_fleet():
    assert validate_text("fleet", GOOD_FLEET)["ok"] is True


def test_validate_dup_id_fleet():
    res = validate_text("fleet", DUP_ID_FLEET)
    assert res["ok"] is False and "duplicate node id" in res["error"]


def test_validate_bad_yaml_reports_line():
    res = validate_text("fleet", BAD_YAML)
    assert res["ok"] is False
    assert res["line"]                       # a line number was extracted


def test_validate_bad_profile():
    assert validate_text("profile", BAD_PROFILE)["ok"] is False


def test_validate_good_commands():
    assert validate_text("commands", GOOD_COMMANDS)["ok"] is True


def test_validate_good_networks():
    assert validate_text("networks", GOOD_NETWORKS)["ok"] is True


def test_validate_bad_networks():
    res = validate_text("networks", BAD_NETWORKS)
    assert res["ok"] is False and "key" in res["error"]


def test_validate_script_empty():
    assert validate_text("script", "   ")["ok"] is False
    assert validate_text("script", "#!/bin/sh\necho hi")["ok"] is True


# ---- tree / read ------------------------------------------------------------
def test_list_tree(tmp_path):
    store, _ = make_store(tmp_path)
    tree = store.list_tree()
    keys = {r["key"] for r in tree}
    assert keys == {"fleet", "profiles", "commands"}
    cmd_root = next(r for r in tree if r["key"] == "commands")
    kinds = {f["name"]: f["kind"] for f in cmd_root["files"]}
    assert kinds["commands.yaml"] == "commands"
    assert kinds["archive.sh"] == "script"


def test_read_file(tmp_path):
    store, _ = make_store(tmp_path)
    doc = store.read_file("fleet", "fleet.yaml")
    assert doc and "nodes" in doc["text"] and doc["kind"] == "fleet"


def test_scope_of(tmp_path):
    store, _ = make_store(tmp_path)
    assert store.scope_of("fleet") == "fleet"
    assert store.scope_of("commands") == "commands"
    assert store.scope_of("nope") is None


# ---- path safety ------------------------------------------------------------
def test_traversal_rejected(tmp_path):
    store, _ = make_store(tmp_path)
    assert store.read_file("fleet", "../../etc/passwd") is None
    assert store.read_file("fleet", "../profiles/roleA.yaml") is None
    assert store.write_file("fleet", "../evil.yaml", GOOD_FLEET)["ok"] is False


def test_extension_allowlist(tmp_path):
    store, paths = make_store(tmp_path)
    (paths["fleet_dir"] / "notes.txt").write_text("hi")
    assert store.read_file("fleet", "notes.txt") is None        # .txt not allowed
    assert store.write_file("fleet", "notes.txt", "x")["ok"] is False


def test_dotfile_rejected(tmp_path):
    store, _ = make_store(tmp_path)
    assert store.read_file("fleet", ".bak/fleet.yaml.123") is None


# ---- write / backup / revert ------------------------------------------------
def test_write_rejects_invalid_and_keeps_file(tmp_path):
    store, paths = make_store(tmp_path)
    before = (paths["fleet_dir"] / "fleet.yaml").read_text()
    res = store.write_file("fleet", "fleet.yaml", DUP_ID_FLEET)
    assert res["ok"] is False
    assert (paths["fleet_dir"] / "fleet.yaml").read_text() == before   # untouched


def test_write_good_backs_up_then_revert(tmp_path):
    store, paths = make_store(tmp_path)
    new = GOOD_FLEET.replace("alpha", "square")
    res = store.write_file("fleet", "fleet.yaml", new)
    assert res["ok"] is True and res["backup"]
    assert "square" in (paths["fleet_dir"] / "fleet.yaml").read_text()
    # a backup of the prior version exists
    bak_dir = paths["fleet_dir"] / ".bak"
    assert bak_dir.is_dir() and any(bak_dir.iterdir())
    # revert restores the previous content
    rev = store.revert("fleet", "fleet.yaml")
    assert rev["ok"] is True and "alpha" in rev["text"]
    assert "alpha" in (paths["fleet_dir"] / "fleet.yaml").read_text()


def test_revert_without_backup(tmp_path):
    store, _ = make_store(tmp_path)
    assert store.revert("fleet", "fleet.yaml")["ok"] is False


def test_validate_rejects_bad_path(tmp_path):
    store, _ = make_store(tmp_path)
    assert store.validate("fleet", "evil.sh", "x")["ok"] is False        # wrong ext
    assert store.validate("fleet", "../e.yaml", GOOD_FLEET)["ok"] is False  # traversal


def test_revert_rejects_invalid_backup_and_keeps_live(tmp_path):
    store, paths = make_store(tmp_path)
    # a valid edit → makes a backup of the original
    store.write_file("fleet", "fleet.yaml", GOOD_FLEET.replace("alpha", "square"))
    # corrupt that backup so a naive revert would restore an invalid fleet
    bak = next((paths["fleet_dir"] / ".bak").iterdir())
    bak.write_text(DUP_ID_FLEET)
    res = store.revert("fleet", "fleet.yaml")
    assert res["ok"] is False and "invalid" in res["error"]
    # live file untouched — still the valid 'square' version
    assert "square" in (paths["fleet_dir"] / "fleet.yaml").read_text()


def test_dry_run_blocks_write(tmp_path):
    store, paths = make_store(tmp_path, dry_run=True)
    before = (paths["fleet_dir"] / "fleet.yaml").read_text()
    res = store.write_file("fleet", "fleet.yaml", GOOD_FLEET.replace("alpha", "square"))
    assert res["ok"] is True and res.get("dry_run")
    assert (paths["fleet_dir"] / "fleet.yaml").read_text() == before   # not written
