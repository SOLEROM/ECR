"""Orchestrator fan-out + variant-aware sequences, driven by the mock backend."""

import os
import pytest
import yaml

from core.orchestrator import Orchestrator
from core.mock_ssh import MockFleetState, MockSSHClient
from core.events import EventStream, EventType
from core.commands import CommandCatalog
from core.gates_config import GateRegistry


def _real_gates_dir(tmp_path):
    """A tiny real gates/ dir (reach + process + metric) for the non-mock eval path."""
    gdir = tmp_path / "gates"; gdir.mkdir()
    (gdir / "gateA.yaml").write_text(yaml.safe_dump(
        {"gate": {"key": "A", "kind": "reach", "on": "roleA"}}))
    (gdir / "gateB.yaml").write_text(yaml.safe_dump(
        {"gate": {"key": "B", "kind": "process", "on": "roleA", "processes": [
            {"name": "serviceA", "pattern": "serviceA", "mandatory": True},
            {"name": "serviceB", "pattern": "serviceB", "mandatory": True}]}}))
    (gdir / "gateD.yaml").write_text(yaml.safe_dump(
        {"gate": {"key": "D", "kind": "metric", "on": "roleA",
                  "cmd": "cat /tmp/ccflet/links.count", "parse": "regex",
                  "fields": [{"name": "peers", "pattern": r"(\d+)", "type": "int"}],
                  "levels": [{"when": {"peers": ">=1"}, "color": "green"},
                             {"default": True, "color": "yellow"}]}}))
    return GateRegistry(str(gdir))


def test_evaluate_gates_real_path(fleet, profile_mgr, tmp_path, fake_ssh):
    # not mock → the orchestrator runs the real transport (connect / pgrep / cat) against
    # a FakeSSH: serviceB's check fails (mandatory down → B red), the metric reads 3 peers.
    reg = _real_gates_dir(tmp_path)
    client = fake_ssh(responses=[("serviceB", ("", "", 1)), ("links.count", ("3", "", 0))])
    orch = Orchestrator(fleet, profile_mgr, lambda n, r: client, gates=reg)
    ns = orch.poll_node("d1")
    assert ns.reachable_roleA is True
    assert ns.gates["A"]["state"] == "ok"                      # connect succeeded
    assert ns.gates["B"]["state"] == "fail"                    # serviceB down (mandatory)
    procs = {p["name"]: p["up"] for p in ns.gates["B"]["processes"]}
    assert procs == {"serviceA": True, "serviceB": False}
    assert ns.gates["D"]["state"] == "ok" and ns.gates["D"]["fields"]["peers"] == 3


def test_real_path_reachability_short_circuits(fleet, profile_mgr, tmp_path, fake_ssh):
    reg = _real_gates_dir(tmp_path)
    client = fake_ssh()
    client.connect = lambda: False                            # roleA won't connect
    orch = Orchestrator(fleet, profile_mgr, lambda n, r: client, gates=reg)
    ns = orch.poll_node("d1")
    assert ns.reachable_roleA is False
    assert ns.gates["A"]["state"] == "fail"                   # reach gate down
    assert ns.gates["B"]["state"] == "fail"                   # role gates short-circuit
    assert ns.gates["D"]["state"] == "fail"
    # the short-circuit means no process/metric command was ever run
    assert client.commands == []
    # …but the process gate still LISTS its configured processes (all down) so the
    # per-process LEDs render red instead of collapsing to a blank row.
    procs = {p["name"]: p["up"] for p in ns.gates["B"]["processes"]}
    assert procs == {"serviceA": False, "serviceB": False}


def build_orch(fleet, profile_mgr, tmp_path, systemd=True):
    state = MockFleetState(fleet, systemd_serviceA=systemd)
    factory = lambda node, role: MockSSHClient(state, node, role)  # noqa: E731
    stream = EventStream(os.path.join(tmp_path, "events.jsonl"))
    orch = Orchestrator(fleet, profile_mgr, factory, event_stream=stream)
    return orch, state, stream


def event_types(stream):
    return [e.event_type for e in stream.get_all_events()]


def test_run_action_starts_serviceA(fleet, profile_mgr, tmp_path):
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    r = orch.run_action("d1", "roleA", "serviceA_start")
    assert r.success
    assert state.is_up("d1", "serviceA")


def test_bring_up_variant_a(fleet, profile_mgr, tmp_path):
    fleet.set_variant("A")
    orch, state, stream = build_orch(fleet, profile_mgr, tmp_path)
    results = orch.bring_up("d1")
    assert all(r.success for r in results)
    assert state.is_up("d1", "serviceA") and state.is_up("d1", "serviceB")
    assert not state.is_up("d1", "serviceC")
    types = event_types(stream)
    assert EventType.SEQUENCE_STARTED.value in types
    assert EventType.SEQUENCE_COMPLETED.value in types


def test_bring_up_variant_b_orders_serviceC_first(fleet, profile_mgr, tmp_path):
    fleet.set_variant("B")
    orch, state, stream = build_orch(fleet, profile_mgr, tmp_path)
    orch.bring_up("d1")
    assert state.is_up("d1", "serviceC")
    assert state.is_up("d1", "serviceA")
    assert state.is_up("d1", "serviceB")
    # ordering: serviceC_start step appears before serviceA_start step
    steps = [e.data.get("step") for e in stream.get_all_events()
             if e.event_type == EventType.SEQUENCE_STEP.value]
    assert steps.index("serviceC_start") < steps.index("serviceA_start") < steps.index("serviceB_start")


def test_tear_down_reverses(fleet, profile_mgr, tmp_path):
    fleet.set_variant("B")
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    orch.bring_up("d1")
    orch.tear_down("d1")
    assert not state.is_up("d1", "serviceB")
    assert not state.is_up("d1", "serviceA")
    assert not state.is_up("d1", "serviceC")


def test_mixed_variants_sequence_per_node(fleet, profile_mgr, tmp_path):
    # the whole point of per-node variant: a mixed-variant fleet brings each node up
    # by *its own* variant — d1 (B) gets serviceC, d2 (A) does not.
    fleet.set_node_variant("d1", "B")
    fleet.set_node_variant("d2", "A")
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    orch.bring_up("d1")
    orch.bring_up("d2")
    assert state.is_up("d1", "serviceC")          # variant B → serviceC brought up
    assert not state.is_up("d2", "serviceC")      # variant A → no serviceC
    assert state.is_up("d1", "serviceA") and state.is_up("d1", "serviceB")
    assert state.is_up("d2", "serviceA") and state.is_up("d2", "serviceB")
    # gates reflect each node's own variant: d1 (B) runs the serviceC process check (proc
    # gate ok with all three up); d2 (A) has no variant-B check gate at all (na).
    assert orch.poll_node("d1").gates["B"]["state"] == "ok"
    assert orch.poll_node("d2").gates["C"]["state"] == "na"


def test_deploy_sets_deployed(fleet, profile_mgr, tmp_path):
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    results = orch.deploy("d1", build=True)
    assert all(r.success for r in results)
    assert state.deployed["d1"] is True
    assert state.built["d1"] is True


def test_fan_out_all_nodes(fleet, profile_mgr, tmp_path):
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    res = orch.fan_out("roleA", "serviceA_start", fleet.names())
    assert set(res.keys()) == set(fleet.names())
    assert all(r.success for r in res.values())
    assert all(state.is_up(n, "serviceA") for n in fleet.names())


def test_poll_node_gates_after_bringup(fleet, profile_mgr, tmp_path):
    fleet.set_variant("A")
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    orch.bring_up_fleet(fleet.names())
    ns = orch.poll_node("d1")
    assert ns.gates["A"]["state"] == "ok"
    assert ns.gates["B"]["state"] == "ok"
    assert ns.gates["D"]["state"] == "ok"     # link metric healthy once serviceA is up


def test_poll_before_bringup_is_red(fleet, profile_mgr, tmp_path):
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    ns = orch.poll_node("d1")
    assert ns.gates["B"]["state"] == "fail"   # nothing running yet


def test_full_fleet_arc_variant_b(fleet, profile_mgr, tmp_path):
    fleet.set_variant("B")
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    orch.bring_up_fleet(fleet.names())
    orch.poll_all()
    for n in fleet.names():
        g = orch.statuses[n].gates
        assert g["A"]["state"] == "ok"
        assert g["B"]["state"] == "ok"
        assert g["C"]["state"] == "ok"
        assert g["D"]["state"] == "ok"


def test_offline_node_unreachable(fleet, profile_mgr, tmp_path):
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    state.set_offline("d3", True)
    ns = orch.poll_node("d3")
    assert ns.reachable_roleA is False
    assert ns.gates["A"]["state"] == "fail"
    # an offline node still lists its process LEDs (all down) — same as the real path
    procs = {p["name"]: p["up"] for p in ns.gates["B"]["processes"]}
    assert procs and all(up is False for up in procs.values())


def test_dry_run_does_not_change_state(fleet, profile_mgr, tmp_path):
    state = MockFleetState(fleet)
    factory = lambda node, role: MockSSHClient(state, node, role)  # noqa: E731
    orch = Orchestrator(fleet, profile_mgr, factory, dry_run=True)
    r = orch.run_action("d1", "roleA", "serviceA_start")
    assert r.success and r.stdout.startswith("[dry-run]")
    assert not state.is_up("d1", "serviceA")


# ---- hot reload -------------------------------------------------------------
def test_mock_reload_keeps_surviving_node_state(fleet, profile_mgr, tmp_path):
    orch, state, _ = build_orch(fleet, profile_mgr, tmp_path)
    orch.run_action("d1", "roleA", "serviceA_start")
    assert state.is_up("d1", "serviceA")
    # reload the fleet: drop d3, add x9 (same in-place Fleet object)
    fleet.reload_from_dict({"fleet": {"defaults": {"variant": fleet.default_variant, "algo": fleet.algo},
        "nodes": [
            {"name": "d1", "id": 1, "host": "10.0.0.101", "subnet": "10.1.1"},
            {"name": "d2", "id": 2, "host": "10.0.0.102", "subnet": "10.1.2"},
            {"name": "x9", "id": 9, "host": "10.0.0.109", "subnet": "10.1.9"},
        ]}})
    state.reload(fleet)
    orch.sync_node_locks()
    assert state.is_up("d1", "serviceA")      # surviving node kept its state
    assert "x9" in state.daemons              # new node present
    assert "d3" not in state.daemons          # removed node gone
    r = orch.run_action("x9", "roleA", "serviceA_start")   # engine drives the new node
    assert r.success and state.is_up("x9", "serviceA")


def test_reload_profiles_resnapshots(fleet, profile_mgr, tmp_path):
    orch, _, _ = build_orch(fleet, profile_mgr, tmp_path)
    profile_mgr.invalidate()
    orch.reload_profiles(profile_mgr)
    assert orch.profiles["roleA"] is not None
    assert orch.profiles["roleB"] is not None


# ---- operator commands ------------------------------------------------------
def build_cmd_orch(fleet, profile_mgr, tmp_path, catalog, scripts=None, mock=True):
    cdir = tmp_path / "commands"; cdir.mkdir()
    (cdir / "commands.yaml").write_text(yaml.safe_dump(catalog))
    for fn, body in (scripts or {}).items():
        (cdir / fn).write_text(body)
    state = MockFleetState(fleet)
    factory = lambda node, role: MockSSHClient(state, node, role)  # noqa: E731
    stream = EventStream(os.path.join(tmp_path, "ev.jsonl"))
    orch = Orchestrator(fleet, profile_mgr, factory, event_stream=stream)
    cat = CommandCatalog(str(cdir / "commands.yaml"))
    orch.configure_commands(cat, str(cdir), allow_local=True,
                            runs_dir=str(tmp_path), mock=mock)
    return orch, state, stream


def test_run_custom_remote_node_renders_params(fleet, profile_mgr, tmp_path):
    cat = {"commands": {"df": {"label": "D", "on": "remote", "role": "roleA",
                               "scope": "node", "run": "df -h {DEPLOY_ROOT}"}}}
    orch, _, stream = build_cmd_orch(fleet, profile_mgr, tmp_path, cat)
    res = orch.run_custom("df", node="d1")
    assert res["ok"] and len(res["results"]) == 1
    assert "df -h /srv/ccfleet/roleA" in res["results"][0]["stdout"]   # {DEPLOY_ROOT} rendered
    types = event_types(stream)
    assert EventType.ACTION_STARTED.value in types
    assert EventType.ACTION_COMPLETED.value in types


def test_run_custom_remote_fleet_fanout(fleet, profile_mgr, tmp_path):
    cat = {"commands": {"up": {"label": "U", "on": "remote", "role": "roleA",
                               "scope": "fleet", "run": "uptime"}}}
    orch, _, _ = build_cmd_orch(fleet, profile_mgr, tmp_path, cat)
    res = orch.run_custom("up", nodes=["d1", "d2"])
    assert res["ok"] and len(res["results"]) == 2


def test_run_custom_local_is_echo_only_in_mock(fleet, profile_mgr, tmp_path):
    marker = tmp_path / "ran.marker"
    cat = {"commands": {"mk": {"label": "M", "on": "local", "scope": "fleet",
                               "run": f"touch {marker}"}}}
    orch, _, _ = build_cmd_orch(fleet, profile_mgr, tmp_path, cat)   # mock=True
    res = orch.run_custom("mk")
    assert res["ok"]
    assert res["results"][0]["stdout"].startswith("[dry-run] (local)")
    assert res["results"][0]["extra"]["on"] == "local"
    assert not marker.exists()        # the command was NOT actually run on the host


def test_run_custom_local_real_subprocess(fleet, profile_mgr, tmp_path):
    cat = {"commands": {"hi": {"label": "H", "on": "local", "scope": "fleet",
                               "run": "echo hello-local"}}}
    orch, _, _ = build_cmd_orch(fleet, profile_mgr, tmp_path, cat, mock=False)
    res = orch.run_custom("hi")
    assert res["ok"] and "hello-local" in res["results"][0]["stdout"]


def test_run_custom_local_disabled(fleet, profile_mgr, tmp_path):
    cat = {"commands": {"d": {"label": "D", "on": "local", "scope": "fleet",
                              "run": "df -h"}}}
    orch, _, _ = build_cmd_orch(fleet, profile_mgr, tmp_path, cat)
    orch.allow_local = False
    res = orch.run_custom("d")
    assert res["ok"] is False
    assert "disabled" in res["results"][0]["stderr"]


def test_run_custom_unknown(fleet, profile_mgr, tmp_path):
    orch, _, _ = build_cmd_orch(fleet, profile_mgr, tmp_path, {"commands": {}})
    res = orch.run_custom("nope", node="d1")
    assert res["ok"] is False and "unknown" in res["error"]


def test_run_custom_danger_flagged_and_audited(fleet, profile_mgr, tmp_path):
    cat = {"commands": {"reboot": {"label": "R", "on": "remote", "role": "roleA",
                                   "scope": "node", "run": "sudo reboot", "danger": True}}}
    orch, _, stream = build_cmd_orch(fleet, profile_mgr, tmp_path, cat)
    res = orch.run_custom("reboot", node="d1")
    assert res["danger"] is True
    started = [e for e in stream.get_all_events()
               if e.event_type == EventType.ACTION_STARTED.value]
    assert started[-1].data["danger"] is True


def test_run_custom_dry_run_does_not_execute(fleet, profile_mgr, tmp_path):
    cat = {"commands": {"df": {"label": "D", "on": "remote", "role": "roleA",
                               "scope": "node", "run": "df -h"}}}
    cdir = tmp_path / "commands"; cdir.mkdir()
    (cdir / "commands.yaml").write_text(yaml.safe_dump(cat))
    state = MockFleetState(fleet)
    factory = lambda node, role: MockSSHClient(state, node, role)  # noqa: E731
    orch = Orchestrator(fleet, profile_mgr, factory, dry_run=True)
    orch.configure_commands(CommandCatalog(str(cdir / "commands.yaml")), str(cdir))
    res = orch.run_custom("df", node="d1")
    assert res["ok"] and res["results"][0]["stdout"].startswith("[dry-run]")
