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
        networks_path=str(tmp_path / "networks" / "networks.yaml"),
        runs_dir=str(tmp_path / "runs"),
        mock=True, poll=False)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


# ---- custom commands --------------------------------------------------------
def test_node_command_runs_node_scope(client):
    r = client.post("/api/node/node1/command", json={"command": "df_data"})
    assert r.status_code == 200 and r.get_json()["ok"]


def test_node_command_rejects_fleet_scope(client):
    # 'uptime' is scope: fleet in the shipped catalog → must not run via /node/
    r = client.post("/api/node/node1/command", json={"command": "uptime"})
    assert r.status_code == 400
    assert "fleet-scoped" in r.get_json()["error"]


def test_node_command_unknown(client):
    r = client.post("/api/node/node1/command", json={"command": "nope"})
    assert r.status_code == 400


def test_fleet_command_local_echo_only(client):
    r = client.post("/api/fleet/command", json={"command": "base_disk"})
    body = r.get_json()
    assert body["ok"] and body["results"][0]["extra"]["on"] == "local"
    assert body["results"][0]["stdout"].startswith("[dry-run] (local)")  # mock → echo


# ---- connectivity LEDs ------------------------------------------------------
def test_networks_endpoint_lists_links(client):
    body = client.get("/api/networks").get_json()
    keys = [l["key"] for l in body["links"]]
    assert "link1" in keys and "link2" in keys and "link3" in keys
    assert body["poll_interval"] > 0


def test_networks_refresh_endpoint_ok(client):
    assert client.post("/api/networks/refresh").get_json()["success"]


def test_networks_simulated_all_up(client):
    # mock → the monitor simulates (all up) without pinging. Poll synchronously
    # (the refresh endpoint runs it on a background thread) so the assert can't race.
    client.application.ccflet.net_monitor.poll_once()
    body = client.get("/api/networks").get_json()
    assert body["links"] and all(l["up"] is True for l in body["links"])


def test_networks_edit_hot_reloads_links(client):
    # read the current link3 host from the API rather than hard-coding it, so the test
    # can't go stale when the shipped networks.yaml changes its address.
    before = {l["key"]: l["host"] for l in client.get("/api/networks").get_json()["links"]}
    doc = client.get("/api/config/file?root=networks&path=networks.yaml").get_json()
    new = doc["text"].replace(before["link3"], "10.9.9.9")
    assert new != doc["text"]                       # the edit actually changed something
    r = client.post("/api/config/file",
                    json={"root": "networks", "path": "networks.yaml", "text": new})
    assert r.status_code == 200 and r.get_json()["ok"] and r.get_json()["reloaded"]
    hosts = {l["key"]: l["host"] for l in client.get("/api/networks").get_json()["links"]}
    assert hosts["link3"] == "10.9.9.9"


# ---- config editor ----------------------------------------------------------
def test_config_validate_rejects_bad_path(client):
    r = client.post("/api/config/validate",
                    json={"root": "fleet", "path": "evil.sh", "text": "x"})
    assert r.get_json()["ok"] is False


def test_config_save_reload_and_revert(client):
    doc = client.get("/api/config/file?root=fleet&path=fleet.yaml").get_json()
    new = doc["text"].replace("10.0.0.101", "10.0.0.190")
    r = client.post("/api/config/file",
                    json={"root": "fleet", "path": "fleet.yaml", "text": new})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["reloaded"]
    # hot-reloaded: the live fleet reflects the edit
    node1 = [n for n in client.get("/api/fleet").get_json()["nodes"] if n["name"] == "node1"][0]
    assert node1["host"] == "10.0.0.190"
    # revert restores
    rv = client.post("/api/config/revert", json={"root": "fleet", "path": "fleet.yaml"})
    assert rv.status_code == 200 and rv.get_json()["ok"]


def test_config_save_invalid_blocked(client):
    bad = "fleet:\n  nodes:\n    - {name: d1, id: 1, host: h, subnet: 10.0.0}\n" \
          "    - {name: d2, id: 1, host: h2, subnet: 10.0.1}\n"   # dup id
    r = client.post("/api/config/file",
                    json={"root": "fleet", "path": "fleet.yaml", "text": bad})
    assert r.status_code == 400 and r.get_json()["ok"] is False
