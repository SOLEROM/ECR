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
# the States root validates two shapes (ping links + cmd states) by content:
GOOD_STATES_PING = "networks:\n  links:\n    - {key: link1, label: Gateway, host: 10.0.0.1}\n"
BAD_STATES_PING = "networks:\n  links:\n    - {key: 'bad key', host: h}\n"
GOOD_STATES_CMD = ("states:\n  probes:\n"
                   "    - {key: disk, cmd: 'true', return_colors: {0: green, 1: red}}\n")
BAD_STATES_CMD = ("states:\n  probes:\n"
                  "    - {key: disk, cmd: 'true', return_colors: {0: chartreuse}}\n")
GOOD_LOGS = "logs:\n  windows:\n    - {key: syslog, process: rsyslogd, path: /var/log/syslog}\n"
BAD_LOGS = "logs:\n  windows:\n    - {key: syslog, process: rsyslogd}\n"   # missing path
# the Gates root validates one health gate per file (gates_config):
GOOD_GATES = "gate:\n  key: A\n  label: reach\n  kind: reach\n  on: roleA\n"
BAD_GATES = "gate:\n  key: A\n  kind: reach\n  colors: {up: chartreuse}\n"   # bad color
BAD_GATES_WHEN = ("gate:\n  key: C\n  kind: metric\n  cmd: x\n"
                  "  fields: [{name: v, pattern: '(\\d+)', type: int}]\n"
                  "  levels:\n    - {when: {v: '>=1'}, color: green}\n")   # no default level


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


def test_validate_good_states_ping():
    assert validate_text("states", GOOD_STATES_PING)["ok"] is True


def test_validate_bad_states_ping():
    res = validate_text("states", BAD_STATES_PING)
    assert res["ok"] is False and "key" in res["error"]


def test_validate_good_states_cmd():
    assert validate_text("states", GOOD_STATES_CMD)["ok"] is True


def test_validate_bad_states_cmd():
    res = validate_text("states", BAD_STATES_CMD)
    assert res["ok"] is False and "color" in res["error"]


def test_validate_good_gates():
    assert validate_text("gates", GOOD_GATES)["ok"] is True


def test_validate_bad_gates_color():
    res = validate_text("gates", BAD_GATES)
    assert res["ok"] is False and "color" in res["error"]


def test_validate_bad_gates_missing_default_level():
    res = validate_text("gates", BAD_GATES_WHEN)
    assert res["ok"] is False and "default" in res["error"]


def test_validate_good_logs():
    assert validate_text("logs", GOOD_LOGS)["ok"] is True


def test_validate_bad_logs():
    res = validate_text("logs", BAD_LOGS)
    assert res["ok"] is False and "path" in res["error"]


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


def test_logs_root_registered(tmp_path):
    fleet_dir = tmp_path / "fleet"; fleet_dir.mkdir()
    prof_dir = tmp_path / "profiles"; prof_dir.mkdir()
    logs_dir = tmp_path / "logs"; logs_dir.mkdir()
    (fleet_dir / "fleet.yaml").write_text(GOOD_FLEET)
    (prof_dir / "roleA.yaml").write_text(MIN_PROFILE)
    (logs_dir / "logs.yaml").write_text(GOOD_LOGS)
    roots = default_roots(str(fleet_dir / "fleet.yaml"), str(prof_dir),
                          logs_dir=str(logs_dir))
    store = ConfigStore(roots)
    assert store.scope_of("logs") == "logs"
    logs_root = next(r for r in store.list_tree() if r["key"] == "logs")
    assert logs_root["label"] == "Logs"
    assert {f["name"]: f["kind"] for f in logs_root["files"]}["logs.yaml"] == "logs"
    # a good logs file writes; an invalid one (missing path) is rejected before write
    assert store.write_file("logs", "logs.yaml", GOOD_LOGS)["ok"] is True
    assert store.write_file("logs", "logs.yaml", BAD_LOGS)["ok"] is False


def test_gates_root_registered(tmp_path):
    fleet_dir = tmp_path / "fleet"; fleet_dir.mkdir()
    prof_dir = tmp_path / "profiles"; prof_dir.mkdir()
    gates_dir = tmp_path / "gates"; gates_dir.mkdir()
    (fleet_dir / "fleet.yaml").write_text(GOOD_FLEET)
    (prof_dir / "roleA.yaml").write_text(MIN_PROFILE)
    (gates_dir / "gateA.yaml").write_text(GOOD_GATES)
    roots = default_roots(str(fleet_dir / "fleet.yaml"), str(prof_dir),
                          gates_dir=str(gates_dir))
    store = ConfigStore(roots)
    assert store.scope_of("gates") == "gates"
    gates_root = next(r for r in store.list_tree() if r["key"] == "gates")
    assert gates_root["label"] == "Gates"
    assert {f["name"]: f["kind"] for f in gates_root["files"]}["gateA.yaml"] == "gates"
    # a good gate writes; a bad color is rejected before write; revert restores it
    assert store.write_file("gates", "gateA.yaml", GOOD_GATES)["ok"] is True
    assert store.write_file("gates", "gateA.yaml", BAD_GATES)["ok"] is False
    assert store.revert("gates", "gateA.yaml")["ok"] is True


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
