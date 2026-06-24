"""Pure-engine tests for the config-driven gates (core/gates_config.py).

Covers schema parse/validate per kind, field extraction (regex + json), condition + level
evaluation, color→severity, variant gating, the registry (load / reload-in-place / cross-
file key clash), and the orchestrator's real (non-mock) gate evaluation against a fake
client. No network, no subprocess — the I/O is injected.
"""

import pytest
import yaml

from core import gates_config as GC
from core.status import OK, WARN, FAIL, NA, overall_gate


# --- schema: reach -----------------------------------------------------------
def test_reach_gate_defaults_and_colors():
    g = GC.gate_from_dict({"gate": {"key": "A", "kind": "reach"}})
    assert g.kind == "reach" and g.on == "base"          # reach defaults to base
    assert g.method == "ssh" and g.colors == {"up": "green", "down": "red"}
    assert g.applies_to("A") and g.applies_to("B")        # variants: null ⇒ all


def test_on_key_survives_yaml_bool_gotcha():
    # PyYAML parses a bare `on:` key as the boolean True; the parser must still read it,
    # since operators naturally write `on: roleB`.
    block = yaml.safe_load("gate:\n  key: D\n  kind: metric\n  on: roleB\n"
                           "  cmd: x\n  levels: [{default: true, color: green}]\n")
    assert True in block["gate"]              # confirms the gotcha is present
    assert GC.gate_from_dict(block).on == "roleB"
    # quoted key also works
    assert GC.gate_from_dict(yaml.safe_load(
        'gate: {key: A, kind: reach, "on": roleA}')).on == "roleA"


def test_reach_ping_needs_host():
    with pytest.raises(ValueError):
        GC.gate_from_dict({"gate": {"key": "A", "kind": "reach", "method": "ping"}})


def test_reach_host_param_token_ok_but_bad_literal_rejected():
    GC.gate_from_dict({"gate": {"key": "A", "kind": "reach", "method": "ping",
                                "host": "{HOST_A}"}})            # {param} ok
    with pytest.raises(ValueError):
        GC.gate_from_dict({"gate": {"key": "A", "kind": "reach", "method": "ping",
                                    "host": "bad host!"}})


def test_bad_color_is_rejected():
    with pytest.raises(ValueError):
        GC.gate_from_dict({"gate": {"key": "A", "kind": "reach",
                                    "colors": {"up": "chartreuse"}}})


# --- schema: process ---------------------------------------------------------
def test_process_gate_parses_entries_and_variants():
    g = GC.gate_from_dict({"gate": {"key": "B", "kind": "process", "on": "roleA",
        "processes": [
            {"name": "serviceA", "mandatory": True},
            {"name": "serviceC", "pattern": "serviceC", "mandatory": True, "variants": ["B"]},
        ]}})
    assert [p.name for p in g.processes] == ["serviceA", "serviceC"]
    assert g.processes[0].pattern == "serviceA"           # pattern defaults to name
    assert g.processes[1].variants == ("B",)
    assert g.check == GC.DEFAULT_CHECK


def test_process_gate_meta_carries_processes():
    # the UI pre-renders the per-process LEDs from the meta (always visible, default down),
    # so a process gate's meta must list its processes (name/mandatory/variants).
    g = GC.gate_from_dict({"gate": {"key": "B", "kind": "process", "on": "roleA",
        "processes": [
            {"name": "serviceA", "mandatory": True},
            {"name": "serviceC", "mandatory": True, "variants": ["B"]},
        ]}})
    meta = g.to_meta()
    assert meta["processes"] == [
        {"name": "serviceA", "mandatory": True, "variants": None},
        {"name": "serviceC", "mandatory": True, "variants": ["B"]},
    ]
    # non-process gates carry no process list at all.
    assert "processes" not in GC.gate_from_dict(
        {"gate": {"key": "A", "kind": "reach"}}).to_meta()


def test_process_gate_needs_processes():
    with pytest.raises(ValueError):
        GC.gate_from_dict({"gate": {"key": "B", "kind": "process", "processes": []}})


def test_process_duplicate_name_rejected():
    with pytest.raises(ValueError):
        GC.gate_from_dict({"gate": {"key": "B", "kind": "process", "processes": [
            {"name": "x"}, {"name": "x"}]}})


# --- schema: metric ----------------------------------------------------------
METRIC = {"gate": {"key": "C", "kind": "metric", "on": "roleA",
                   "cmd": "read", "parse": "regex",
                   "fields": [{"name": "value", "pattern": r"value=(\d+)", "type": "int"}],
                   "detail": "value={value}",
                   "levels": [{"when": {"value": ">=3"}, "color": "green"},
                              {"default": True, "color": "red", "detail": "low"}]}}


def test_metric_gate_parses():
    g = GC.gate_from_dict(METRIC)
    assert g.cmd == "read" and g.parse == "regex"
    assert g.fields[0].name == "value" and g.fields[0].type == "int"
    assert g.levels[-1].default and g.levels[-1].color == "red"


def test_metric_needs_default_level():
    bad = {"gate": {"key": "C", "kind": "metric", "cmd": "x",
                    "fields": [{"name": "v", "pattern": "(\\d+)", "type": "int"}],
                    "levels": [{"when": {"v": ">=1"}, "color": "green"}]}}
    with pytest.raises(ValueError):
        GC.gate_from_dict(bad)


def test_metric_bad_regex_rejected():
    bad = {"gate": {"key": "C", "kind": "metric", "cmd": "x",
                    "fields": [{"name": "v", "pattern": "(", "type": "int"}],
                    "levels": [{"default": True, "color": "red"}]}}
    with pytest.raises(ValueError):
        GC.gate_from_dict(bad)


def test_unknown_kind_and_bad_key():
    with pytest.raises(ValueError):
        GC.gate_from_dict({"gate": {"key": "A", "kind": "telepathy"}})
    with pytest.raises(ValueError):
        GC.gate_from_dict({"gate": {"key": "bad key", "kind": "reach"}})


# --- field extraction --------------------------------------------------------
def test_extract_fields_regex_types():
    g = GC.gate_from_dict({"gate": {"key": "C", "kind": "metric", "cmd": "x",
        "fields": [{"name": "n", "pattern": r"n=(\d+)", "type": "int"},
                   {"name": "f", "pattern": r"f=([\d.]+)", "type": "float"},
                   {"name": "ok", "pattern": r"ok=(\d+)", "type": "bool"}],
        "levels": [{"default": True, "color": "green"}]}})
    out = GC.extract_fields("n=11 f=3.5 ok=1", g.fields, "regex")
    assert out == {"n": 11, "f": 3.5, "ok": True}


def test_extract_fields_regex_missing_is_omitted():
    g = GC.gate_from_dict({"gate": {"key": "C", "kind": "metric", "cmd": "x",
        "fields": [{"name": "n", "pattern": r"n=(\d+)", "type": "int"}],
        "levels": [{"default": True, "color": "green"}]}})
    assert GC.extract_fields("nothing", g.fields, "regex") == {}


def test_extract_fields_json_dotted_key():
    g = GC.gate_from_dict({"gate": {"key": "C", "kind": "metric", "cmd": "x",
        "parse": "json",
        "fields": [{"name": "sats", "key": "gps.sats", "type": "int"},
                   {"name": "lock", "key": "gps.lock", "type": "bool"}],
        "levels": [{"default": True, "color": "green"}]}})
    out = GC.extract_fields('{"gps": {"sats": 9, "lock": 1}}', g.fields, "json")
    assert out == {"sats": 9, "lock": True}


# --- conditions + levels -----------------------------------------------------
@pytest.mark.parametrize("value,cond,expected", [
    (9, ">=9", True), (8, ">=9", False), (5, ">5", False), (6, ">5", True),
    (3, "<=3", True), (2, "<3", True), (3, "==3", True), (4, "==3", False),
    (7, "5..9", True), (10, "5..9", False),
    (True, True, True), (False, True, False), (None, False, True),
    ("up", "==up", True), ("up", "down", False), (None, ">=1", False),
])
def test_match_condition(value, cond, expected):
    assert GC.match_condition(value, cond) is expected


def test_evaluate_levels_first_match_wins():
    levels = (GC.Level(color="green", when={"v": ">=3"}),
              GC.Level(color="yellow", when={"v": ">=1"}),
              GC.Level(color="red", default=True))
    assert GC.evaluate_levels({"v": 5}, levels).color == "green"
    assert GC.evaluate_levels({"v": 2}, levels).color == "yellow"
    assert GC.evaluate_levels({"v": 0}, levels).color == "red"
    assert GC.evaluate_levels({}, levels).color == "red"        # missing → default


def test_render_detail_fills_and_dashes():
    assert GC.render_detail("v={v} s={s}", {"v": 3}) == "v=3 s=—"


# --- color → severity --------------------------------------------------------
def test_color_to_severity():
    assert GC.color_to_severity("green") == OK
    assert GC.color_to_severity("blue") == OK
    assert GC.color_to_severity("yellow") == WARN
    assert GC.color_to_severity("orange") == WARN
    assert GC.color_to_severity("red") == FAIL
    assert GC.color_to_severity("gray") == NA


def test_gate_result_carries_color_and_state():
    g = GC.gate_from_dict({"gate": {"key": "A", "kind": "reach"}})
    r = GC.gate_result(g, "yellow", "warn detail")
    assert r["color"] == "yellow" and r["state"] == WARN
    assert r["key"] == "A" and r["kind"] == "reach"
    assert overall_gate({"A": r}) == WARN


def test_na_result_is_gray_na():
    g = GC.gate_from_dict({"gate": {"key": "C", "kind": "reach", "variants": ["B"]}})
    assert g.applies_to("B") and not g.applies_to("A")
    r = GC.na_result(g)
    assert r["color"] == "gray" and r["state"] == NA
    assert overall_gate({"C": r}) == NA            # na ignored by the rollup


# --- file validation (config-store entry point) ------------------------------
def test_gate_file_from_dict_ok_and_bad():
    assert GC.gate_file_from_dict(METRIC).key == "C"
    with pytest.raises(ValueError):
        GC.gate_file_from_dict({"nope": 1})        # no gate block
    with pytest.raises(ValueError):
        GC.gate_file_from_dict([1, 2, 3])          # not a mapping


# --- registry ----------------------------------------------------------------
def _write_gate(d, name, body):
    (d / name).write_text(yaml.safe_dump(body))


def test_registry_loads_orders_and_metas(tmp_path):
    _write_gate(tmp_path, "z.yaml", {"gate": {"key": "Z", "kind": "reach", "order": 5}})
    _write_gate(tmp_path, "a.yaml", {"gate": {"key": "A", "kind": "reach", "order": 1}})
    reg = GC.GateRegistry(str(tmp_path))
    assert [s.key for s in reg.specs] == ["A", "Z"]         # by order then key
    assert {m["key"] for m in reg.metas()} == {"A", "Z"}
    assert reg.by_key("A").kind == "reach"


def test_registry_skips_broken_file(tmp_path):
    _write_gate(tmp_path, "good.yaml", {"gate": {"key": "A", "kind": "reach"}})
    (tmp_path / "broken.yaml").write_text("gate:\n  key: B\n  kind: nonsense\n")
    reg = GC.GateRegistry(str(tmp_path))
    assert [s.key for s in reg.specs] == ["A"]              # broken one skipped


def test_registry_cross_file_key_clash_first_wins(tmp_path):
    _write_gate(tmp_path, "a.yaml", {"gate": {"key": "A", "kind": "reach", "label": "first"}})
    _write_gate(tmp_path, "b.yaml", {"gate": {"key": "A", "kind": "reach", "label": "second"}})
    reg = GC.GateRegistry(str(tmp_path))
    assert len(reg.specs) == 1 and reg.specs[0].label == "first"


def test_registry_reload_in_place(tmp_path):
    _write_gate(tmp_path, "a.yaml", {"gate": {"key": "A", "kind": "reach"}})
    reg = GC.GateRegistry(str(tmp_path))
    assert [s.key for s in reg.specs] == ["A"]
    _write_gate(tmp_path, "b.yaml", {"gate": {"key": "B", "kind": "reach"}})
    reg.reload()
    assert [s.key for s in reg.specs] == ["A", "B"]


def test_registry_poll_interval_is_min(tmp_path):
    _write_gate(tmp_path, "a.yaml", {"gate": {"key": "A", "kind": "reach", "interval": 9}})
    _write_gate(tmp_path, "b.yaml", {"gate": {"key": "B", "kind": "reach", "interval": 3}})
    assert GC.GateRegistry(str(tmp_path)).poll_interval == 3.0
