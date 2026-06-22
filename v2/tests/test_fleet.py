"""Fleet inventory + parameter derivation (per-node variant mechanism)."""

import pytest

from core.fleet import fleet_from_dict, Fleet, Node


def test_load_and_lookup(fleet):
    assert fleet.name == "test-fleet"
    assert fleet.names() == ["d1", "d2", "d3"]
    assert fleet.get("d2").id == 2
    assert fleet.get("nope") is None


def test_params_variant_a(fleet):
    fleet.set_variant("A")
    p = fleet.params(fleet.get("d1"))
    assert p["ID"] == "1"
    assert p["HOST_A"] == "10.0.0.101"
    assert p["SUBNET"] == "10.1.1"
    assert p["HOST_B"] == "10.1.1.2"
    assert p["VAR_ADDR"] == "10.0.0.255"     # variant A → the fixed addr
    assert p["VAR_LAUNCHER"] == "variantA.run"
    assert p["VAR_FLAG"] == ""
    assert p["ALGO"] == "default"
    assert p["VARIANT"] == "A"


def test_params_variant_b(fleet):
    fleet.set_variant("B")
    p = fleet.params(fleet.get("d2"))
    assert p["VAR_ADDR"] == "10.1.2.255"        # <SUBNET>.255
    assert p["VAR_LAUNCHER"] == "variantB.run"
    assert p["VAR_FLAG"] == "--variant-flag"
    assert p["HOST_B"] == "10.1.2.2"
    assert p["VARIANT"] == "B"


def test_params_explicit_variant_override(fleet):
    # current fleet variant is A, but ask for B explicitly
    fleet.set_variant("A")
    p = fleet.params(fleet.get("d1"), variant="B")
    assert p["VAR_LAUNCHER"] == "variantB.run"
    assert p["VAR_ADDR"] == "10.1.1.255"


def test_node_algo_override():
    fd = {"fleet": {"name": "f", "defaults": {"algo": "default"},
                    "nodes": [{"name": "a", "id": 1, "host": "h", "subnet": "10.0.0",
                               "algo": "square"}]}}
    f = fleet_from_dict(fd)
    assert f.params(f.get("a"))["ALGO"] == "square"


# ---- per-node variant -------------------------------------------------------
def test_node_variant_override_from_yaml():
    # a node may set its own variant; nodes that don't inherit the fleet default
    fd = {"fleet": {"defaults": {"variant": "A"}, "nodes": [
        {"name": "va", "id": 1, "host": "h1", "subnet": "10.0.0"},
        {"name": "vb", "id": 2, "host": "h2", "subnet": "10.0.1", "variant": "B"},
    ]}}
    f = fleet_from_dict(fd)
    assert f.node_variant("va") == "A"          # inherits default
    assert f.node_variant("vb") == "B"          # own variant


def test_node_variant_invalid_rejected():
    fd = {"fleet": {"nodes": [
        {"name": "a", "id": 1, "host": "h", "subnet": "10.0.0", "variant": "X"}]}}
    with pytest.raises(ValueError, match="invalid variant"):
        fleet_from_dict(fd)


def test_set_node_variant_validates(fleet):
    with pytest.raises(ValueError):              # bad variant token
        fleet.set_node_variant("d1", "C")
    with pytest.raises(ValueError, match="unknown node"):
        fleet.set_node_variant("nope", "B")


def test_params_uses_node_variant_without_explicit_arg(fleet):
    # params() derives from each node's own live variant (no explicit variant= passed)
    fleet.set_node_variant("d1", "A")
    fleet.set_node_variant("d2", "B")
    pa = fleet.params(fleet.get("d1"))
    pb = fleet.params(fleet.get("d2"))
    assert pa["VARIANT"] == "A" and pa["VAR_LAUNCHER"] == "variantA.run" and pa["VAR_FLAG"] == ""
    assert pb["VARIANT"] == "B" and pb["VAR_LAUNCHER"] == "variantB.run" and pb["VAR_FLAG"] == "--variant-flag"
    assert pa["VAR_ADDR"] == "10.0.0.255"        # variant A → the fixed addr
    assert pb["VAR_ADDR"] == "10.1.2.255"        # variant B → <SUBNET>.255


def test_mixed_variants_survive_reload(fleet):
    fleet.set_node_variant("d1", "B")            # one node in B, others A
    fleet.reload_from_dict({"fleet": {"defaults": {"variant": "A"}, "nodes": [
        {"name": "d1", "id": 1, "host": "h1", "subnet": "10.0.0"},      # surviving
        {"name": "d2", "id": 2, "host": "h2", "subnet": "10.0.1"},      # surviving
        {"name": "x9", "id": 9, "host": "h9", "subnet": "10.0.9", "variant": "B"},  # new
    ]}})
    assert fleet.node_variant("d1") == "B"       # surviving node keeps its live variant
    assert fleet.node_variant("d2") == "A"       # surviving default-variant node unchanged
    assert fleet.node_variant("x9") == "B"       # new node takes its configured variant


def test_node_variant_serialized_in_yaml(fleet):
    fleet.set_node_variant("d1", "B")
    again = fleet_from_dict(__import__("yaml").safe_load(fleet.to_yaml()))
    assert again.node_variant("d1") == "B"       # live per-node variant round-trips


def test_connection_params_present(fleet):
    p = fleet.params(fleet.get("d1"))
    assert p["roleA_user"] == "user"
    assert p["roleB_user"] == "root"
    assert "BatchMode" in p["ssh_opts"]
    assert p["DEPLOY_ROOT"] == "/srv/ccfleet/roleA"


def test_variants_overridable_from_defaults():
    # a custom variants block replaces the built-in placeholders
    fd = {"fleet": {"defaults": {
        "variants": {"A": {"addr": "172.16.0.255", "launcher": "go.sh", "flag": "--a"}}},
        "nodes": [{"name": "a", "id": 1, "host": "h", "subnet": "10.0.0"}]}}
    f = fleet_from_dict(fd)
    p = f.params(f.get("a"), variant="A")
    assert p["VAR_ADDR"] == "172.16.0.255"
    assert p["VAR_LAUNCHER"] == "go.sh"
    assert p["VAR_FLAG"] == "--a"


def test_roleB_host_suffix_configurable():
    fd = {"fleet": {"defaults": {"roleB_host_suffix": ".254"},
                    "nodes": [{"name": "a", "id": 1, "host": "h", "subnet": "10.9.9"}]}}
    f = fleet_from_dict(fd)
    assert f.params(f.get("a"))["HOST_B"] == "10.9.9.254"


def test_duplicate_id_rejected():
    fd = {"fleet": {"nodes": [
        {"name": "a", "id": 1, "host": "h1", "subnet": "10.0.0"},
        {"name": "b", "id": 1, "host": "h2", "subnet": "10.0.1"},
    ]}}
    with pytest.raises(ValueError, match="duplicate node id"):
        fleet_from_dict(fd)


def test_duplicate_name_rejected():
    fd = {"fleet": {"nodes": [
        {"name": "a", "id": 1, "host": "h1", "subnet": "10.0.0"},
        {"name": "a", "id": 2, "host": "h2", "subnet": "10.0.1"},
    ]}}
    with pytest.raises(ValueError, match="duplicate node name"):
        fleet_from_dict(fd)


def test_empty_fleet_rejected():
    with pytest.raises(ValueError, match="no nodes"):
        fleet_from_dict({"fleet": {"nodes": []}})


def test_invalid_default_variant_rejected():
    fd = {"fleet": {"defaults": {"variant": "X"},
                    "nodes": [{"name": "a", "id": 1, "host": "h", "subnet": "10.0.0"}]}}
    with pytest.raises(ValueError, match="invalid default variant"):
        fleet_from_dict(fd)


def test_set_variant_validates(fleet):
    with pytest.raises(ValueError):
        fleet.set_variant("C")


def test_set_algo_rejects_shell_metachars(fleet):
    for bad in ["a; rm -rf /", "a b", "$(whoami)", "x`id`", "a|b", ""]:
        with pytest.raises(ValueError):
            fleet.set_algo(bad)
    fleet.set_algo("square")
    assert fleet.algo == "square"
    assert fleet.params(fleet.get("d1"))["ALGO"] == "square"


def test_missing_required_field_rejected():
    fd = {"fleet": {"nodes": [{"name": "a", "id": 1}]}}
    with pytest.raises(ValueError, match="missing required"):
        fleet_from_dict(fd)


def test_roundtrip_yaml(fleet):
    text = fleet.to_yaml()
    again = fleet_from_dict(__import__("yaml").safe_load(text))
    assert again.names() == fleet.names()


# ---- hot reload -------------------------------------------------------------
def test_reload_from_dict_updates_nodes(fleet):
    new = {"fleet": {"name": "renamed", "defaults": {"variant": "A", "algo": "default"},
                     "nodes": [
                         {"name": "d1", "id": 1, "host": "10.0.0.1", "subnet": "10.0.0"},
                         {"name": "x9", "id": 9, "host": "10.0.0.9", "subnet": "10.0.9"},
                     ]}}
    fleet.reload_from_dict(new)
    assert fleet.name == "renamed"
    assert fleet.names() == ["d1", "x9"]
    assert fleet.get("d1").host == "10.0.0.1"        # edited in place
    assert fleet.get("d2") is None                   # removed node is gone


def test_reload_preserves_live_variant_and_algo(fleet):
    # operator runtime selection must survive a fleet.yaml edit (it is set from the
    # dashboard, not the file) — even though the new file defaults to A/default.
    fleet.set_variant("B")                   # bulk: every surviving node → B
    fleet.set_algo("square")
    fleet.reload_from_dict({"fleet": {"defaults": {"variant": "A", "algo": "default"},
                                      "nodes": [{"name": "d1", "id": 1,
                                                 "host": "h", "subnet": "10.0.0"}]}})
    assert fleet.node_variant("d1") == "B"   # live per-node variant preserved
    assert fleet.default_variant == "A"      # default tracks the new file
    assert fleet.algo == "square"


def test_node_name_must_be_bare_token():
    fd = {"fleet": {"nodes": [{"name": "bad name", "id": 1, "host": "h", "subnet": "10.0.0"}]}}
    with pytest.raises(ValueError, match="node name"):
        fleet_from_dict(fd)
    fd = {"fleet": {"nodes": [{"name": "d$(id)", "id": 1, "host": "h", "subnet": "10.0.0"}]}}
    with pytest.raises(ValueError, match="node name"):
        fleet_from_dict(fd)


def test_node_host_must_be_host_token():
    fd = {"fleet": {"nodes": [{"name": "a", "id": 1, "host": "h;rm -rf /", "subnet": "10.0.0"}]}}
    with pytest.raises(ValueError, match="host token"):
        fleet_from_dict(fd)


def test_fleet_name_must_be_bare_token():
    fd = {"fleet": {"name": "bad name", "nodes": [
        {"name": "a", "id": 1, "host": "h", "subnet": "10.0.0"}]}}
    with pytest.raises(ValueError, match="fleet name"):
        fleet_from_dict(fd)


def test_reload_rejects_invalid_and_keeps_old(fleet):
    before = fleet.names()
    bad = {"fleet": {"nodes": [
        {"name": "a", "id": 1, "host": "h1", "subnet": "10.0.0"},
        {"name": "b", "id": 1, "host": "h2", "subnet": "10.0.1"},   # dup id
    ]}}
    with pytest.raises(ValueError, match="duplicate node id"):
        fleet.reload_from_dict(bad)
    assert fleet.names() == before                  # unchanged on a bad edit


# ---- selection groups (dashboard Select line) -------------------------------
def _fleet_with_groups(groups):
    return fleet_from_dict({"fleet": {
        "nodes": [
            {"name": "d1", "id": 1, "host": "h1", "subnet": "10.0.0"},
            {"name": "d2", "id": 2, "host": "h2", "subnet": "10.0.1"},
            {"name": "d3", "id": 3, "host": "h3", "subnet": "10.0.2"},
        ],
        "groups": groups,
    }})


def test_no_groups_is_empty(fleet):
    assert fleet.groups == []
    assert fleet.groups_as_list() == []


def test_groups_parsed_in_order_with_dedup():
    f = _fleet_with_groups({"front": ["d1", "d2"], "all": ["d1", "d2", "d2", "d3"]})
    assert [g["name"] for g in f.groups_as_list()] == ["front", "all"]
    assert f.groups_as_list()[0]["nodes"] == ["d1", "d2"]
    assert f.groups_as_list()[1]["nodes"] == ["d1", "d2", "d3"]   # d2 de-duped


def test_groups_allow_spaces_in_name():
    f = _fleet_with_groups({"Front row": ["d1"]})
    assert f.groups_as_list()[0]["name"] == "Front row"


def test_groups_reject_unknown_node():
    with pytest.raises(ValueError, match="unknown node 'd9'"):
        _fleet_with_groups({"front": ["d1", "d9"]})


def test_groups_reject_non_list_members():
    with pytest.raises(ValueError, match="must be a list"):
        _fleet_with_groups({"front": "d1"})


def test_groups_reject_non_mapping():
    with pytest.raises(ValueError, match="'groups' must be a mapping"):
        fleet_from_dict({"fleet": {
            "nodes": [{"name": "d1", "id": 1, "host": "h", "subnet": "10.0.0"}],
            "groups": ["d1"],
        }})


def test_groups_roundtrip_yaml():
    f = _fleet_with_groups({"front": ["d1", "d2"], "rear": ["d3"]})
    again = fleet_from_dict(__import__("yaml").safe_load(f.to_yaml()))
    assert again.groups_as_list() == f.groups_as_list()


def test_groups_reject_stale_member_on_reload(fleet):
    # a group naming a node that the edited inventory no longer contains is a likely
    # operator typo → rejected with a clear message, old fleet kept (atomic edit).
    with pytest.raises(ValueError, match="unknown node 'd2'"):
        fleet.reload_from_dict({"fleet": {
            "nodes": [{"name": "d1", "id": 1, "host": "h1", "subnet": "10.0.0"}],
            "groups": {"front": ["d1", "d2"]},
        }})


def test_groups_updated_on_reload(fleet):
    fleet.reload_from_dict({"fleet": {
        "nodes": [{"name": "d1", "id": 1, "host": "h", "subnet": "10.0.0"}],
        "groups": {"solo": ["d1"]},
    }})
    assert fleet.groups_as_list() == [{"name": "solo", "nodes": ["d1"]}]
