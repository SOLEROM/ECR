"""HTTP-layer checks for the D8 config/command endpoints (Flask test client).

Config writes are isolated to a temp copy of the default profile's config roots
(yamls/default/{fleet,profiles,commands,…}) so the repo files are never touched.
"""

import os
import shutil

import pytest

import app as appmod

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(tmp_path):
    src = os.path.join(HERE, "yamls", "default")
    for sub in ("fleet", "profiles", "commands", "networks", "logs", "gates"):
        shutil.copytree(os.path.join(src, sub), tmp_path / sub)
    (tmp_path / "runs").mkdir()
    # explicit per-root overrides isolate every config root into tmp_path, regardless of
    # which profile is persisted as active (explicit args win over the active profile).
    flask_app, _socketio, _cc = appmod.create_app(
        fleet_path=str(tmp_path / "fleet" / "fleet.yaml"),
        profiles_dir=str(tmp_path / "profiles"),
        commands_dir=str(tmp_path / "commands"),
        states_dir=str(tmp_path / "networks"),
        logs_dir=str(tmp_path / "logs"),
        gates_dir=str(tmp_path / "gates"),
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


# ---- health gates (config-driven) -------------------------------------------
def test_gates_endpoint_lists(client):
    body = client.get("/api/gates").get_json()
    assert body["gates"] and all(g["key"] and g["kind"] in ("reach", "process", "metric")
                                 for g in body["gates"])


def test_gates_drive_node_status(client):
    # under --mock the gate cells come from the simulate hook; before any bring-up the
    # proc gate is red/fail and the reach gate green/ok.
    node = _first_node(client)
    ns = client.get(f"/api/node/{node}/status").get_json()
    assert ns["gates"]["A"]["state"] == "ok"           # reachable
    assert ns["gates"]["B"]["state"] == "fail"         # nothing running yet
    assert ns["gates"]["A"]["color"] == "green"        # cells carry a named color too


def test_gates_edit_hot_reloads(client):
    # relabel a gate live (read its current label from the API), then confirm /api/gates
    # reflects it — the same validate→write→reload path as the other config roots.
    gate = client.get("/api/gates").get_json()["gates"][0]
    doc = client.get(f"/api/config/file?root=gates&path=gate{gate['key']}.yaml").get_json()
    new = doc["text"].replace(f"label: {gate['label']}", "label: ready")
    assert new != doc["text"]
    r = client.post("/api/config/file",
                    json={"root": "gates", "path": f"gate{gate['key']}.yaml", "text": new})
    assert r.status_code == 200 and r.get_json()["ok"] and r.get_json()["reloaded"]
    labels = [g["label"] for g in client.get("/api/gates").get_json()["gates"]]
    assert "ready" in labels


# ---- Logs view (base-station log windows) -----------------------------------
def test_logs_endpoint_lists(client):
    body = client.get("/api/logs").get_json()
    assert body["windows"] and all(w["key"] and w["path"] for w in body["windows"])
    assert body["enabled"] is False        # mock → simulated panes, not real tailing


def test_logs_edit_hot_reloads(client):
    # relabel the first window live (read its current label from the API so the test
    # follows a fork that renames its windows), then confirm /api/logs reflects it.
    win = client.get("/api/logs").get_json()["windows"][0]
    old_label = win["label"]
    doc = client.get("/api/config/file?root=logs&path=logs.yaml").get_json()
    new = doc["text"].replace(old_label, "Kernel ring", 1)
    assert new != doc["text"]              # the edit actually changed something
    r = client.post("/api/config/file",
                    json={"root": "logs", "path": "logs.yaml", "text": new})
    assert r.status_code == 200 and r.get_json()["ok"] and r.get_json()["reloaded"]
    labels = [w["label"] for w in client.get("/api/logs").get_json()["windows"]]
    assert "Kernel ring" in labels


def test_export_captures_log_artifacts(client):
    cc = client.application.ccflet
    sid = cc.current_id
    r = client.get(f"/sessions/{sid}/export")
    assert r.status_code == 200
    art = os.path.join(cc.sessions.runs_dir, sid, "artifacts", "logs")
    files = sorted(os.listdir(art))
    # one artifact per configured window, named <key>.log
    keys = {w["key"] for w in client.get("/api/logs").get_json()["windows"]}
    assert files and {f[:-4] for f in files} == keys
    # under mock the capture is echo-only (no real base-station file is read)
    with open(os.path.join(art, files[0]), encoding="utf-8") as fh:
        assert "simulated run" in fh.read()


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


# ---- config profiles (the switchable editable-YAML sets, P8) -----------------
def test_config_profiles_listed(client):
    # the tree response carries the active profile + the profile list (one round-trip)
    tree = client.get("/api/config/tree").get_json()
    assert tree["active"] == "default" and "default" in tree["profiles"]
    profs = client.get("/api/config/profiles").get_json()
    assert profs["active"] == "default" and "default" in profs["profiles"]


def test_config_profile_switch_unknown_rejected(client):
    r = client.post("/api/config/profile", json={"name": "no_such_profile"})
    assert r.status_code == 400 and r.get_json()["ok"] is False
    # the active profile is unchanged by a rejected switch
    assert client.get("/api/config/profiles").get_json()["active"] == "default"


def test_config_profile_new_rejects_bad_name(client):
    # 'default' is reserved; rejected before anything is written
    r = client.post("/api/config/profile/new", json={"name": "default"})
    assert r.status_code == 400 and r.get_json()["ok"] is False
