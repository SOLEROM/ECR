"""HTTP-layer checks for the D8 config/command endpoints (Flask test client).

Config writes are isolated to a temp copy of fleet/ profiles/ commands/ so the repo
files are never touched.
"""

import os
import shutil

import pytest

import app as appmod

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(tmp_path):
    for sub in ("fleet", "profiles", "commands", "networks"):
        shutil.copytree(os.path.join(HERE, sub), tmp_path / sub)
    (tmp_path / "runs").mkdir()
    flask_app, _socketio, _cc = appmod.create_app(
        fleet_path=str(tmp_path / "fleet" / "fleet.yaml"),
        profiles_dir=str(tmp_path / "profiles"),
        commands_dir=str(tmp_path / "commands"),
        states_dir=str(tmp_path / "networks"),
        runs_dir=str(tmp_path / "runs"),
        mock=True, poll=False)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


# ---- live discovery helpers (spec-resilient: read names from the API, never hard-code
# the demo's node/command/link names, so a fork that rewrites config keeps these green) -
def _first_node(client):
    return client.get("/api/fleet").get_json()["nodes"][0]["name"]


def _commands(client):
    return client.get("/api/commands").get_json()["commands"]


def _find_command(client, **match):
    """First command meta matching all of the given fields (e.g. scope='node'); None."""
    for m in _commands(client):
        if all(m.get(k) == v for k, v in match.items()):
            return m
    return None


# ---- custom commands --------------------------------------------------------
def test_node_command_runs_node_scope(client):
    # a node-scoped command reachable in the default variant (local, or remote/roleA —
    # roleB needs variant B). Discover it instead of pinning the demo's `df_data`.
    cmd = (_find_command(client, scope="node", on="local")
           or _find_command(client, scope="node", on="remote", role="roleA"))
    if not cmd:
        pytest.skip("no node-scoped local/roleA command in this app's catalog")
    r = client.post(f"/api/node/{_first_node(client)}/command",
                    json={"command": cmd["name"]})
    assert r.status_code == 200 and r.get_json()["ok"]


def test_node_command_rejects_fleet_scope(client):
    # a fleet-scoped command must not run via the single-node endpoint
    cmd = _find_command(client, scope="fleet")
    if not cmd:
        pytest.skip("no fleet-scoped command in this app's catalog")
    r = client.post(f"/api/node/{_first_node(client)}/command",
                    json={"command": cmd["name"]})
    assert r.status_code == 400
    assert "fleet-scoped" in r.get_json()["error"]


def test_node_command_unknown(client):
    r = client.post(f"/api/node/{_first_node(client)}/command",
                    json={"command": "definitely_not_a_command"})
    assert r.status_code == 400


def test_fleet_command_local_echo_only(client):
    cmd = _find_command(client, on="local")
    if not cmd:
        pytest.skip("no local command in this app's catalog")
    r = client.post("/api/fleet/command", json={"command": cmd["name"]})
    body = r.get_json()
    assert body["ok"] and body["results"][0]["extra"]["on"] == "local"
    assert body["results"][0]["stdout"].startswith("[dry-run] (local)")  # mock → echo


# ---- status LEDs (the States bar) -------------------------------------------
def test_states_endpoint_lists(client):
    body = client.get("/api/states").get_json()
    assert body["states"] and all(s["key"] and s["color"] and s["kind"] in ("ping", "cmd")
                                  for s in body["states"])
    assert body["poll_interval"] > 0


def test_states_refresh_endpoint_ok(client):
    assert client.post("/api/states/refresh").get_json()["success"]


def test_states_simulated_all_green(client):
    # mock → the monitor simulates (healthy) without pinging or running shell. Poll
    # synchronously (refresh runs on a background thread) so the assert can't race.
    client.application.ccflet.state_monitor.poll_once()
    body = client.get("/api/states").get_json()
    assert body["states"] and all(s["color"] == "green" for s in body["states"])


def test_states_edit_hot_reloads(client):
    # relabel the first ping state live (read its current label from the API rather than
    # hard-coding a name), so the test follows a fork that renames its links.
    states = client.get("/api/states").get_json()["states"]
    ping = next(s for s in states if s["kind"] == "ping")
    old_label = ping["label"]
    doc = client.get("/api/config/file?root=states&path=networks.yaml").get_json()
    new = doc["text"].replace(old_label, "EdgeLink")
    assert new != doc["text"]                       # the edit actually changed something
    r = client.post("/api/config/file",
                    json={"root": "states", "path": "networks.yaml", "text": new})
    assert r.status_code == 200 and r.get_json()["ok"] and r.get_json()["reloaded"]
    labels = [s["label"] for s in client.get("/api/states").get_json()["states"]]
    assert "EdgeLink" in labels


# ---- config editor ----------------------------------------------------------
def test_config_validate_rejects_bad_path(client):
    r = client.post("/api/config/validate",
                    json={"root": "fleet", "path": "evil.sh", "text": "x"})
    assert r.get_json()["ok"] is False


def test_config_save_reload_and_revert(client):
    # read the first node + its host from the live fleet rather than pinning 10.0.0.101,
    # so the test follows a fork with a different seed inventory.
    node = client.get("/api/fleet").get_json()["nodes"][0]
    name, old_host = node["name"], node["host"]
    doc = client.get("/api/config/file?root=fleet&path=fleet.yaml").get_json()
    new = doc["text"].replace(old_host, "10.0.0.190")
    assert new != doc["text"]
    r = client.post("/api/config/file",
                    json={"root": "fleet", "path": "fleet.yaml", "text": new})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["reloaded"]
    # hot-reloaded: the live fleet reflects the edit
    edited = [n for n in client.get("/api/fleet").get_json()["nodes"] if n["name"] == name][0]
    assert edited["host"] == "10.0.0.190"
    # revert restores
    rv = client.post("/api/config/revert", json={"root": "fleet", "path": "fleet.yaml"})
    assert rv.status_code == 200 and rv.get_json()["ok"]


def test_config_save_invalid_blocked(client):
    bad = "fleet:\n  nodes:\n    - {name: d1, id: 1, host: h, subnet: 10.0.0}\n" \
          "    - {name: d2, id: 1, host: h2, subnet: 10.0.1}\n"   # dup id
    r = client.post("/api/config/file",
                    json={"root": "fleet", "path": "fleet.yaml", "text": bad})
    assert r.status_code == 400 and r.get_json()["ok"] is False
