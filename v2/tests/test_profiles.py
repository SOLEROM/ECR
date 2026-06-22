"""Profile loading, action schema, and {param} rendering."""

import pytest

from core.profiles import (
    profile_from_dict, substitute, extract_params, render_action,
    render_connection, ACTION_KINDS,
)


def test_load_roleA_profile(profile_mgr):
    roleA = profile_mgr.load("roleA")
    assert roleA is not None
    assert "serviceA_start" in roleA.actions
    assert roleA.action("serviceA_start").kind == "daemon"
    assert roleA.action("serviceA_start").daemon == "serviceA"
    assert roleA.action("serviceA_start").prefer_systemd == "serviceA"
    assert roleA.action("deploy_serviceB").kind == "transfer"
    assert roleA.action("deploy_serviceB").method == "rsync"
    assert "links" in roleA.collectors
    assert roleA.logs["rx"] == "/tmp/serviceA.rx"


def test_load_roleB_profile_via_jumphost(profile_mgr):
    roleB = profile_mgr.load("roleB")
    assert roleB.connection.via == "{roleA_user}@{HOST_A}"
    assert roleB.action("serviceC_start").daemon == "serviceC"
    assert "probeA_status" in roleB.actions


def test_render_action(fleet, profile_mgr):
    fleet.set_variant("B")
    p = fleet.params(fleet.get("d2"))
    a = render_action(profile_mgr.load("roleA").action("serviceA_start"), p)
    assert "ID=2" in a.command
    assert "ADDR=10.1.2.255" in a.command
    assert "./variantB.run tcp" in a.command


def test_render_action_variant_a_flag_empty(fleet, profile_mgr):
    fleet.set_variant("A")
    p = fleet.params(fleet.get("d1"))
    a = render_action(profile_mgr.load("roleA").action("serviceB_start"), p)
    assert "--algo default" in a.command
    assert "--variant-flag" not in a.command


def test_render_connection(fleet, profile_mgr):
    fleet.set_variant("B")
    p = fleet.params(fleet.get("d1"))
    c = render_connection(profile_mgr.load("roleB").connection, p)
    assert c.host == "10.1.1.2"
    assert c.via == "user@10.0.0.101"


def test_substitute_unknown_left_verbatim():
    assert substitute("a {x} {y}", {"x": "1"}) == "a 1 {y}"


def test_extract_params():
    assert extract_params("cd {DEPLOY_ROOT} && {VAR_LAUNCHER}") == ["DEPLOY_ROOT", "VAR_LAUNCHER"]


def test_invalid_kind_rejected():
    with pytest.raises(ValueError, match="invalid kind"):
        profile_from_dict({"actions": {"x": {"kind": "bogus"}}})


def test_transfer_without_method_rejected():
    with pytest.raises(ValueError, match="transfer needs method"):
        profile_from_dict({"actions": {"x": {"kind": "transfer"}}})


def test_action_kinds_constant():
    assert set(ACTION_KINDS) == {"transfer", "exec", "daemon",
                                 "daemon_stop", "daemon_status"}
