"""Base-station ping links (the ping kind of "state"): model parsing/validation.

This is the kept ping model (``core/networks.py``), now one of two state-source kinds.
The runtime poller + the cmd kind + the registry live in ``tests/test_states.py``.
"""

import os
import pytest

from core.networks import (
    networks_from_dict, load_networks, Networks, NetLink,
)

GOOD = {"networks": {"poll_interval": 2, "ping_timeout": 1, "links": [
    {"key": "link1", "label": "Gateway", "host": "10.0.0.1"},
    {"key": "link2", "label": "Upstream", "host": "10.0.0.2", "hint": "upstream check"},
]}}


# ---- model ------------------------------------------------------------------
def test_parse_links():
    n = networks_from_dict(GOOD)
    assert [l.key for l in n.links] == ["link1", "link2"]
    assert n.poll_interval == 2 and n.ping_timeout == 1
    assert n.links[1].hint == "upstream check"


def test_label_defaults_to_key():
    n = networks_from_dict({"networks": {"links": [{"key": "r", "host": "h"}]}})
    assert n.links[0].label == "r"
    assert n.poll_interval > 0 and n.ping_timeout > 0   # defaults applied


def test_bad_key_rejected():
    with pytest.raises(ValueError, match="key"):
        networks_from_dict({"networks": {"links": [{"key": "bad key", "host": "h"}]}})


def test_bad_host_rejected():
    # a shell-metachar host must not slip through to the (argv) ping
    with pytest.raises(ValueError, match="host"):
        networks_from_dict({"networks": {"links": [{"key": "r", "host": "1.2.3.4; rm -rf /"}]}})


def test_duplicate_key_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        networks_from_dict({"networks": {"links": [
            {"key": "r", "host": "h1"}, {"key": "r", "host": "h2"}]}})


def test_bad_poll_interval_rejected():
    with pytest.raises(ValueError, match="poll_interval"):
        networks_from_dict({"networks": {"poll_interval": 0, "links": []}})


def test_empty_links_ok():
    assert networks_from_dict({"networks": {"links": []}}).links == []


def test_reload_in_place():
    n = networks_from_dict(GOOD)
    same = n
    n.reload_from_dict({"networks": {"links": [{"key": "x", "host": "h"}]}})
    assert n is same                                   # mutated in place (held refs survive)
    assert [l.key for l in n.links] == ["x"]


def test_meta_carries_ui_fields():
    m = NetLink("link1", "Gateway", "10.0.0.1", "the gw").to_meta()
    assert m == {"key": "link1", "label": "Gateway", "host": "10.0.0.1", "hint": "the gw"}


# ---- loading the shipped file ----------------------------------------------
def test_load_missing_file_is_empty(tmp_path):
    assert load_networks(str(tmp_path / "nope.yaml")).links == []


def test_load_shipped_file():
    # structural, not name-pinned: the shipped file parses to ≥1 valid link (key + host),
    # so a fork that renames its links keeps this green.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    n = load_networks(os.path.join(here, "yamls", "default", "networks", "networks.yaml"))
    assert n.links and all(l.key and l.host for l in n.links)
    assert n.poll_interval > 0 and n.ping_timeout > 0
