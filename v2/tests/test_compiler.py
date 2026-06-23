"""Compiler unit tests — the deterministic transforms + bookkeeping (no network).

These stay pure/fast (tmp dirs only, no fork/server) so they're safe to run inside a
forked app during the acceptance gate.
"""

import os

import yaml

from compiler import build as B
from compiler import catalog, fork, spec, stages
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


def test_emit_profiles_from_subpart(tmp_path):
    sub = {"roleA-profile": {
        "extends": "profiles.roleA",
        "connection": {"user": "{roleA_user}", "host": "{HOST_A}"},
        "actions": {
            "serviceA_start": {"kind": "daemon", "name": "serviceA",
                               "command": "run {ID}"},
            "deploy_serviceA": {"kind": "transfer", "method": "rsync",
                                "src": "./a/", "dst": "{DEPLOY_ROOT}/a/"},
        },
        "collectors": {"links": {"command": "cat links", "parser": "link"}},
        "logs": {"rx": "/tmp/x"}}}
    man = Manifest(app_dir=str(tmp_path))
    report = []
    B.emit_profiles(str(tmp_path), sub, man, report)
    doc = yaml.safe_load((tmp_path / "profiles" / "roleA.yaml").read_text())
    assert doc["name"] == "roleA"
    assert "extends" not in doc                          # the patch key is stripped
    # loads cleanly through the engine's own profile validator
    from core.profiles import profile_from_dict
    prof = profile_from_dict(doc, name="roleA")
    assert prof.action("serviceA_start").kind == "daemon"
    assert prof.action("deploy_serviceA").method == "rsync"
    assert "profiles/roleA.yaml" in man.owned


def test_emit_profiles_skipped_keeps_template_default(tmp_path):
    # no *-profile sub-part → nothing written (template profile is kept, R2)
    man = Manifest(app_dir=str(tmp_path))
    B.emit_profiles(str(tmp_path), {}, man, [])
    assert not (tmp_path / "profiles").exists()
    assert man.owned == {}


def test_emit_profiles_invalid_aborts(tmp_path):
    import pytest
    sub = {"roleB-profile": {"actions": {"bad": {"kind": "bogus"}}}}
    with pytest.raises(ValueError, match="roleB-profile: invalid profile"):
        B.emit_profiles(str(tmp_path), sub, Manifest(app_dir=str(tmp_path)), [])


def test_catalog_profile_defaults_are_valid_profiles():
    """The shipped roleA/roleB profile defaults parse through the engine validator."""
    from core.profiles import profile_from_dict
    for stem, role in (("roleA-profile", "roleA"), ("roleB-profile", "roleB")):
        _desc, body, mode = catalog.part_default(stem)
        assert mode == "live"
        prof = profile_from_dict({k: v for k, v in body.items() if k != "extends"},
                                 name=role)
        assert prof.name == role and prof.actions


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


def test_patch_gate_contract_strings(tmp_path):
    """A gate-* `contract:` block patches the string-contract constants (with comments
    preserved), so a fork can rename [CHECK]/PROBEA to its own vocabulary."""
    gates_dir = tmp_path / "domain"
    gates_dir.mkdir()
    (gates_dir / "gates.py").write_text(
        'CHECK_TAG = "[CHECK]"           # path-1 tag\n'
        'CHECK2_TAG = "[CHECK2]"         # path-2 tag\n'
        'PROBE_A_READY = "PROBEA: READY" # probe a\n'
        'CHECK_GOOD = 3                  # good value\n')
    sub = {"gate-a": {"contract": {"PROBE_A_READY": "PROBEA: CALIBRATED"}},
           "gate-c": {"contract": {"CHECK_TAG": "[HUM]", "CHECK2_TAG": "[HUM2]"},
                      "thresholds": {"CHECK_GOOD": 7}}}
    report = []
    B.patch_gate_thresholds(str(tmp_path), sub, Manifest(app_dir=str(tmp_path)), report)
    src = (gates_dir / "gates.py").read_text()
    # the generated module must still be valid Python with the new vocabulary
    ns = {}
    exec(src, ns)
    assert ns["CHECK_TAG"] == "[HUM]" and ns["CHECK2_TAG"] == "[HUM2]"
    assert ns["PROBE_A_READY"] == "PROBEA: CALIBRATED"
    assert ns["CHECK_GOOD"] == 7
    assert "# path-1 tag" in src                       # inline comments preserved
    assert any("contract" in line for line in report)


def test_patch_gate_contract_noop_without_block(tmp_path):
    gates_dir = tmp_path / "domain"
    gates_dir.mkdir()
    (gates_dir / "gates.py").write_text('CHECK_TAG = "[CHECK]"\n')
    B.patch_gate_thresholds(str(tmp_path), {"gate-c": {}},
                            Manifest(app_dir=str(tmp_path)), [])
    assert (gates_dir / "gates.py").read_text() == 'CHECK_TAG = "[CHECK]"\n'


def test_catalog_gate_contract_defaults():
    _d, body_a, _m = catalog.part_default("gate-a")
    assert body_a["contract"]["PROBE_A_READY"] == "PROBEA: READY"
    _d, body_c, _m = catalog.part_default("gate-c")
    assert body_c["contract"]["CHECK_TAG"] == "[CHECK]"


def test_build_writes_manifest_and_report(tmp_path):
    man, report = B.build(str(tmp_path), DEMO_PARAMS, {})
    assert "domain/identity.py" in man.owned and "fleet/fleet.yaml" in man.owned
    assert any("identity:" in line for line in report)
    # the Help tree is regenerated too (front page owned)
    assert "design/00-about.md" in man.owned


# --- build: Help (design/) docs regeneration --------------------------------
def _make_source_docs(tmp_path):
    """A pristine 'template' design/ tree to transform from (no sibling identity.py →
    `_template_app_name` falls back to the demo brand 'ccFleet')."""
    src = tmp_path / "src_design"
    src.mkdir()
    (src / "00-README.md").write_text(
        "---\ntitle: Overview\norder: 0\n---\n\n# ccFleet\n\n"
        "ccFleet uses roleA and serviceA. Brand tokens /tmp/ccflet and CCFlet stay.\n",
        encoding="utf-8")
    (src / "07-health.md").write_text("# Gates\n\nroleA reachable.\n", encoding="utf-8")
    return str(src)


def test_emit_docs_generates_about_and_relabels(tmp_path):
    src = _make_source_docs(tmp_path)
    app = tmp_path / "app"
    man = Manifest(app_dir=str(app))
    B.emit_docs(str(app), DEMO_PARAMS, {}, man, [], source_design=src)

    about = (app / "design" / "00-about.md").read_text()
    assert "About WeatherCtl" in about
    # glossary maps engine keys -> the spec's labels, keeping the engine key as a literal
    assert "station" in about and "collector" in about and "humidity" in about
    assert "`roleA`" in about
    # reference docs: only the *display name* is relabeled
    readme = (app / "design" / "00-README.md").read_text()
    assert "WeatherCtl uses roleA and serviceA" in readme   # display name swapped
    assert "roleA" in readme and "serviceA" in readme       # structural keys kept
    assert "/tmp/ccflet" in readme and "CCFlet" in readme   # brand tokens kept
    assert "ccFleet" not in readme                          # display name fully gone
    assert {"design/00-about.md", "design/00-README.md",
            "design/07-health.md"} <= set(man.owned)


def test_emit_docs_is_idempotent(tmp_path):
    src = _make_source_docs(tmp_path)
    app = tmp_path / "app"
    B.emit_docs(str(app), DEMO_PARAMS, {}, Manifest(app_dir=str(app)), [], source_design=src)
    first = (app / "design" / "00-README.md").read_text()
    B.emit_docs(str(app), DEMO_PARAMS, {}, Manifest(app_dir=str(app)), [], source_design=src)
    assert (app / "design" / "00-README.md").read_text() == first


def test_emit_docs_respects_subpart_flags(tmp_path):
    src = _make_source_docs(tmp_path)
    app = tmp_path / "app"
    sub = {"docs": {"generate_about": False, "relabel_app_name": False,
                    "exclude": ["07-health.md"]}}
    B.emit_docs(str(app), DEMO_PARAMS, sub, Manifest(app_dir=str(app)), [], source_design=src)
    assert not (app / "design" / "00-about.md").exists()       # about suppressed
    readme = (app / "design" / "00-README.md").read_text()
    assert "ccFleet" in readme and "WeatherCtl" not in readme  # relabel off
    assert not (app / "design" / "07-health.md").exists()      # excluded


def test_docs_subcommand_regenerates(tmp_path):
    from compiler import cli
    app = tmp_path / "app"
    (app / "system").mkdir(parents=True)
    (app / "system" / "layer2.params.yaml").write_text("app:\n  name: DocApp\n",
                                                       encoding="utf-8")
    assert cli.main(["--app", str(app), "docs"]) == 0
    about = (app / "design" / "00-about.md")
    assert about.exists() and "About DocApp" in about.read_text()
    assert "design/00-about.md" in Manifest.load(str(app)).owned


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


def test_fork_resets_approved_status_to_draft(tmp_path):
    # a "template" whose demo build.yaml has blessed (approved) params/subparts
    tpl = tmp_path / "tpl"
    (tpl / "system").mkdir(parents=True)
    book = spec.load_build(str(tpl / "system"))
    book.set_status("params", spec.STATUS_APPROVED)
    book.set_status("subparts", spec.STATUS_APPROVED)
    spec.save_build(book)
    # forking must un-bless every stage so the new app's first build runs
    dest = tpl.parent / "apps" / "myapp"
    fork.fork(str(tpl), str(dest))
    forked = spec.load_build(str(dest / "system"))
    assert all(st.status == spec.STATUS_DRAFT for st in forked.stages.values())


# --- catalog + scaffold ------------------------------------------------------
def test_catalog_parts_have_defaults():
    desc, body, mode = catalog.part_default("gate-c")
    assert mode == "frozen" and body["extends"] == "gate.C"
    assert "sequences" in catalog.PARTS and "host-actions" in catalog.PARTS
    ddesc, dbody, dmode = catalog.part_default("docs")
    assert dbody["extends"] == "docs" and dbody["generate_about"] is True


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
