"""Compiler unit tests — the deterministic transforms + bookkeeping (no network).

These stay pure/fast (tmp dirs only, no fork/server) so they're safe to run inside a
forked app during the acceptance gate.
"""

import os

import yaml

from compiler import build as B
from compiler import catalog, spec, stages
from compiler.manifest import Manifest
from compiler.pipeline import _select


# --- build: deterministic emit ----------------------------------------------
DEMO_PARAMS = {
    "app": {
        "name": "WeatherCtl", "fleet_name": "weather", "tagline": "Ops",
        "brand": {"lead": "Wx", "accent": "Ctl"},
        "node": {"count": 2, "represents": "a weather station"},
        "roles": {"roleA": "station", "roleB": "sensorpod"},
        "services": {"serviceA": "collector", "serviceB": "uploader",
                     "serviceC": "calibrator"},
        "variants": {"A": "dry", "B": "live"},
        "gates": {"A": "reach", "B": "procs", "C": "humidity", "D": "uplink"},
    }
}


def test_emit_identity_writes_valid_module(tmp_path):
    man = Manifest(app_dir=str(tmp_path))
    report = []
    B.emit_identity(str(tmp_path), DEMO_PARAMS, {}, man, report)
    path = tmp_path / "domain" / "identity.py"
    assert path.exists()
    ns = {}
    exec(path.read_text(), ns)                      # the generated module is valid Python
    ident = ns["IDENTITY"]
    assert ident["app_name"] == "WeatherCtl"
    assert ident["brand_lead"] == "Wx" and ident["brand_accent"] == "Ctl"
    assert {g["key"] for g in ident["gates"]} == {"A", "B", "C", "D"}
    assert ns["gate_label"]("C") == "humidity"
    assert "domain/identity.py" in man.owned


def test_emit_identity_can_drop_a_gate(tmp_path):
    params = {"app": dict(DEMO_PARAMS["app"], gates={"A": "reach", "B": "procs"})}
    B.emit_identity(str(tmp_path), params, {}, Manifest(app_dir=str(tmp_path)), [])
    ns = {}
    exec((tmp_path / "domain" / "identity.py").read_text(), ns)
    assert [g["key"] for g in ns["IDENTITY"]["gates"]] == ["A", "B"]


def test_emit_fleet_seed_and_count(tmp_path):
    man = Manifest(app_dir=str(tmp_path))
    B.emit_fleet(str(tmp_path), DEMO_PARAMS, man, [])
    doc = yaml.safe_load((tmp_path / "fleet" / "fleet.yaml").read_text())
    assert doc["fleet"]["name"] == "weather"
    assert [n["name"] for n in doc["fleet"]["nodes"]] == ["node1", "node2"]
    # loads cleanly through the engine's own validator
    from core.fleet import fleet_from_dict
    fl = fleet_from_dict(doc)
    assert fl.names() == ["node1", "node2"]


def test_emit_commands_from_action_subpart(tmp_path):
    sub = {"host-actions": {"add": [
        {"id": "disk", "label": "Disk", "run": "df -h", "mode": "live"},
        {"id": "noid_skipped"},  # id present here; test missing-id separately
    ]}}
    man = Manifest(app_dir=str(tmp_path))
    report = []
    B.emit_commands(str(tmp_path), sub, man, report)
    doc = yaml.safe_load((tmp_path / "commands" / "commands_host.yaml").read_text())
    assert "disk" in doc["commands"]
    assert doc["commands"]["disk"]["run"] == "df -h"


def test_emit_networks(tmp_path):
    sub = {"networks": {"poll_interval": 7, "links": [
        {"key": "link1", "label": "GW", "host": "10.0.0.1"}]}}
    B.emit_networks(str(tmp_path), sub, Manifest(app_dir=str(tmp_path)), [])
    doc = yaml.safe_load((tmp_path / "networks" / "networks.yaml").read_text())
    assert doc["networks"]["poll_interval"] == 7
    assert doc["networks"]["links"][0]["key"] == "link1"


def test_patch_gate_thresholds(tmp_path):
    gates_dir = tmp_path / "domain"
    gates_dir.mkdir()
    (gates_dir / "gates.py").write_text("CHECK_GOOD = 3\nSERVICEC_MIN_UP = 15\n")
    sub = {"gate-c": {"thresholds": {"CHECK_GOOD": 7}}}
    report = []
    B.patch_gate_thresholds(str(tmp_path), sub, Manifest(app_dir=str(tmp_path)), report)
    src = (gates_dir / "gates.py").read_text()
    assert "CHECK_GOOD = 7" in src and "SERVICEC_MIN_UP = 15" in src


def test_build_writes_manifest_and_report(tmp_path):
    man, report = B.build(str(tmp_path), DEMO_PARAMS, {})
    assert "domain/identity.py" in man.owned and "fleet/fleet.yaml" in man.owned
    assert any("identity:" in line for line in report)


# --- manifest: drift detection ----------------------------------------------
def test_manifest_check_detects_handedit(tmp_path):
    f = tmp_path / "domain" / "identity.py"
    f.parent.mkdir()
    f.write_text("IDENTITY = {}\n")
    man = Manifest(app_dir=str(tmp_path))
    man.record("domain/identity.py")
    assert man.check() == []                          # unchanged
    f.write_text("IDENTITY = {'hacked': 1}\n")
    assert man.check() == ["domain/identity.py (edited)"]


# --- spec: build.yaml status book -------------------------------------------
def test_build_book_roundtrip(tmp_path):
    sysdir = tmp_path / "system"
    sysdir.mkdir()
    book = spec.load_build(str(sysdir))
    book.app = "demo"
    book.set_status("params", spec.STATUS_APPROVED)
    spec.save_build(book)
    again = spec.load_build(str(sysdir))
    assert again.app == "demo"
    assert again.status_of("params") == spec.STATUS_APPROVED


# --- catalog + scaffold ------------------------------------------------------
def test_catalog_parts_have_defaults():
    desc, body, mode = catalog.part_default("gate-c")
    assert mode == "frozen" and body["extends"] == "gate.C"
    assert "sequences" in catalog.PARTS and "host-actions" in catalog.PARTS


# --- pipeline range selection -----------------------------------------------
def test_select_full_and_partial_ranges():
    assert [t[0] for t in _select("dream", "app", None)] == ["distill", "expand", "build"]
    assert [t[0] for t in _select("subparts", "app", None)] == ["build"]
    assert [t[0] for t in _select("params", "app", None)] == ["expand", "build"]
    assert [t[0] for t in _select("dream", "app", "params")] == ["distill"]


# --- offline distill heuristic ----------------------------------------------
def test_offline_distill_infers_name_and_count():
    dream = "# KioskOps\n\nWe run 8 kiosks in the mall, each with a player.\n"
    params, notes = stages.distill(dream, "offline")
    app = params["app"]
    assert app["name"] == "KioskOps"
    assert app["node"]["count"] == 8
    assert "app" in params and app["defaults"]["roleA_user"] == "user"
