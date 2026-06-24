"""The States subsystem: cmd-state model + classification + registry, and the unified
monitor (with an injected pinger / cmd runner, so no real network or subprocess)."""

import os
import textwrap

import pytest

from core.states import (
    cmd_states_from_dict, state_file_from_dict, StateRegistry, Indicator,
    normalize_color, STATE_COLORS,
)
from core.state_monitor import StateMonitor

CMD_GOOD = {"states": {"poll_interval": 12, "timeout": 3, "probes": [
    {"key": "disk", "label": "Disk", "cmd": "true",
     "return_colors": {0: "green", 1: "red"}, "default_color": "yellow"},
    {"key": "load", "label": "Load", "cmd": "false",
     "return_colors": {0: "green", 2: "red"}},
]}}
PING_GOOD = {"networks": {"links": [
    {"key": "gw", "label": "Gateway", "host": "10.0.0.1"}]}}


# ---- cmd model --------------------------------------------------------------
def test_parse_cmd_states():
    inds, poll = cmd_states_from_dict(CMD_GOOD)
    assert poll == 12
    assert [i.key for i in inds] == ["disk", "load"]
    disk = inds[0]
    assert disk.kind == "cmd" and disk.timeout == 3
    assert disk.color_for_code(0) == "green"
    assert disk.color_for_code(1) == "red"
    assert disk.color_for_code(7) == "yellow"          # falls back to default_color


def test_cmd_label_defaults_to_key():
    inds, _ = cmd_states_from_dict({"states": {"probes": [
        {"key": "x", "cmd": "true", "return_colors": {0: "green"}}]}})
    assert inds[0].label == "x" and inds[0].default_color == "gray"


def test_cmd_bad_key_rejected():
    with pytest.raises(ValueError, match="key"):
        cmd_states_from_dict({"states": {"probes": [
            {"key": "bad key", "cmd": "true", "return_colors": {0: "green"}}]}})


def test_cmd_missing_cmd_rejected():
    with pytest.raises(ValueError, match="cmd"):
        cmd_states_from_dict({"states": {"probes": [
            {"key": "x", "return_colors": {0: "green"}}]}})


def test_cmd_unknown_color_rejected():
    with pytest.raises(ValueError, match="color"):
        cmd_states_from_dict({"states": {"probes": [
            {"key": "x", "cmd": "true", "return_colors": {0: "chartreuse"}}]}})


def test_cmd_non_integer_code_rejected():
    with pytest.raises(ValueError, match="integer"):
        cmd_states_from_dict({"states": {"probes": [
            {"key": "x", "cmd": "true", "return_colors": {"oops": "green"}}]}})


def test_cmd_needs_a_color_mapping():
    with pytest.raises(ValueError, match="return_colors"):
        cmd_states_from_dict({"states": {"probes": [{"key": "x", "cmd": "true"}]}})


def test_cmd_duplicate_key_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        cmd_states_from_dict({"states": {"probes": [
            {"key": "x", "cmd": "true", "return_colors": {0: "green"}},
            {"key": "x", "cmd": "false", "return_colors": {0: "red"}}]}})


def test_color_alias_grey():
    assert normalize_color("GREY", "s") == "gray"
    assert "gray" in STATE_COLORS


# ---- classification ---------------------------------------------------------
def test_classify_cmd_vs_ping():
    cmd_inds, _ = state_file_from_dict(CMD_GOOD, "stateA.yaml")
    assert all(i.kind == "cmd" for i in cmd_inds)
    ping_inds, _ = state_file_from_dict(PING_GOOD, "networks.yaml")
    assert ping_inds and ping_inds[0].kind == "ping" and ping_inds[0].host == "10.0.0.1"


def test_classify_unknown_shape_rejected():
    with pytest.raises(ValueError, match="unrecognized"):
        state_file_from_dict({"something": "else"}, "x.yaml")


# ---- registry (loads a directory of state-source files) ---------------------
def _write(d, name, text):
    (d / name).write_text(textwrap.dedent(text))


def test_registry_merges_ping_and_cmd(tmp_path):
    _write(tmp_path, "networks.yaml",
           "networks:\n  poll_interval: 5\n  links:\n"
           "    - {key: gw, label: GW, host: 10.0.0.1}\n")
    _write(tmp_path, "stateA.yaml",
           "states:\n  poll_interval: 9\n  probes:\n"
           "    - {key: disk, cmd: 'true', return_colors: {0: green}}\n")
    reg = StateRegistry(str(tmp_path))
    assert [i.key for i in reg.indicators] == ["gw", "disk"]   # file-name order
    assert reg.poll_interval == 5                              # min cadence wins


def test_registry_skips_a_broken_file(tmp_path):
    _write(tmp_path, "good.yaml",
           "states:\n  probes:\n    - {key: a, cmd: 'true', return_colors: {0: green}}\n")
    _write(tmp_path, "broken.yaml", "states:\n  probes:\n    - {key: 'bad key'}\n")
    reg = StateRegistry(str(tmp_path))
    assert [i.key for i in reg.indicators] == ["a"]            # broken one skipped, rest survive


def test_registry_dedupes_cross_file_keys(tmp_path):
    _write(tmp_path, "a.yaml",
           "states:\n  probes:\n    - {key: dup, cmd: 'true', return_colors: {0: green}}\n")
    _write(tmp_path, "b.yaml",
           "states:\n  probes:\n    - {key: dup, cmd: 'false', return_colors: {0: red}}\n")
    reg = StateRegistry(str(tmp_path))
    assert [i.key for i in reg.indicators] == ["dup"]          # first file wins


def test_registry_reload_in_place(tmp_path):
    _write(tmp_path, "s.yaml",
           "states:\n  probes:\n    - {key: a, cmd: 'true', return_colors: {0: green}}\n")
    reg = StateRegistry(str(tmp_path))
    same = reg
    _write(tmp_path, "s.yaml",
           "states:\n  probes:\n    - {key: b, cmd: 'true', return_colors: {0: green}}\n")
    reg.reload()
    assert reg is same and [i.key for i in reg.indicators] == ["b"]


def test_registry_missing_dir_is_empty(tmp_path):
    reg = StateRegistry(str(tmp_path / "nope"))
    assert reg.indicators == [] and reg.poll_interval > 0


def test_registry_loads_shipped_dir():
    # structural, not name-pinned: the shipped states dir parses to ≥1 indicator.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    reg = StateRegistry(os.path.join(here, "yamls", "default", "networks"))
    assert reg.indicators and all(i.key and i.kind in ("ping", "cmd") for i in reg.indicators)


# ---- monitor (injected pinger + cmd runner — no real I/O) -------------------
def _reg(tmp_path):
    _write(tmp_path, "networks.yaml",
           "networks:\n  links:\n    - {key: gw, label: GW, host: 10.0.0.1}\n")
    _write(tmp_path, "stateA.yaml",
           "states:\n  probes:\n"
           "    - {key: disk, cmd: 'check', return_colors: {0: green, 1: red}, default_color: yellow}\n")
    return StateRegistry(str(tmp_path))


def test_monitor_ping_and_cmd(tmp_path):
    reg = _reg(tmp_path)
    mon = StateMonitor(reg, simulate=False, allow_local=True,
                       pinger=lambda h, t: True, runner=lambda c, t: 1)
    st = mon.poll_once()
    assert st["gw"]["color"] == "green" and st["gw"]["kind"] == "ping"
    assert st["disk"]["color"] == "red" and st["disk"]["detail"] == "exit 1"


def test_monitor_simulate_no_io(tmp_path):
    reg = _reg(tmp_path)

    def boom(*a):
        raise AssertionError("must not touch I/O when simulating")

    mon = StateMonitor(reg, simulate=True, pinger=boom, runner=boom)
    st = mon.poll_once()
    assert st["gw"]["color"] == "green"           # ping → healthy
    assert st["disk"]["color"] == "green"         # cmd → exit-0 color


def test_monitor_cmd_disabled_when_no_local(tmp_path):
    reg = _reg(tmp_path)
    mon = StateMonitor(reg, simulate=False, allow_local=False,
                       pinger=lambda h, t: True, runner=lambda c, t: 0)
    st = mon.poll_once()
    assert st["gw"]["color"] == "green"           # ping still checked
    assert st["disk"]["color"] == "gray"          # cmd state neutralized


def test_monitor_runner_crash_is_default_color(tmp_path):
    reg = _reg(tmp_path)

    def crash(cmd, timeout):
        raise RuntimeError("boom")

    mon = StateMonitor(reg, simulate=False, runner=crash, pinger=lambda h, t: False)
    st = mon.poll_once()
    assert st["disk"]["color"] == "yellow"        # default_color on a run error


def test_monitor_snapshot_gray_before_poll(tmp_path):
    reg = _reg(tmp_path)
    mon = StateMonitor(reg, simulate=True)
    snap = mon.snapshot()
    assert [s["key"] for s in snap] == ["gw", "disk"]
    assert all(s["color"] == "gray" for s in snap)   # neutral until first poll


def test_monitor_broadcasts_in_order(tmp_path):
    reg = _reg(tmp_path)

    class FakeSync:
        def __init__(self):
            self.calls = []

        def broadcast_states_status(self, states):
            self.calls.append(states)

    fs = FakeSync()
    mon = StateMonitor(reg, sync_manager=fs, simulate=True)
    mon.poll_once()
    assert fs.calls and [s["key"] for s in fs.calls[0]] == ["gw", "disk"]


def test_monitor_on_change_only_on_transition(tmp_path):
    # on_change fires when an LED's color flips between polls — but the first poll is
    # the baseline (no prior reading) so it stays quiet, and a steady color repeats nothing.
    reg = _reg(tmp_path)
    changes = []
    code = {"v": 0}                                   # disk runner exit code (0=green, 1=red)
    mon = StateMonitor(reg, simulate=False, allow_local=True,
                       pinger=lambda h, t: True,      # gw steady-green → never a change
                       runner=lambda c, t: code["v"],
                       on_change=lambda st, old: changes.append((st["key"], old, st["color"])))

    mon.poll_once()                                   # baseline: gw green, disk green
    assert changes == []                              # first poll emits nothing

    code["v"] = 1                                     # disk → red
    mon.poll_once()
    assert changes == [("disk", "green", "red")]      # exactly the transition

    mon.poll_once()                                   # disk still red
    assert len(changes) == 1                          # steady color → no repeat

    code["v"] = 0                                     # disk recovers → green
    mon.poll_once()
    assert changes[-1] == ("disk", "red", "green")


def test_monitor_no_on_change_hook_is_safe(tmp_path):
    # the hook is optional — polling without one must not raise.
    reg = _reg(tmp_path)
    mon = StateMonitor(reg, simulate=False, pinger=lambda h, t: False, runner=lambda c, t: 1)
    mon.poll_once()
    mon.poll_once()                                   # a transition with no hook wired
