"""
Flask routes for ccflet — REST + pages, fleet-first.

Pages: dashboard (node grid + GATE badges), node detail (actions + live log
panes), sessions (audit timeline + ZIP export). Long-running actions (sequences
and fleet fan-outs) are dispatched to background tasks so the request returns at
once; progress streams back over SocketIO. Every action is audited.
"""

import json
import os

from flask import (
    Blueprint, render_template, request, jsonify, send_file, abort,
)

from core import EventStream
from core.docs import build_tree, read_doc

web = Blueprint("web", __name__)

ccflet = None          # CCFletApp facade (set by init_routes)
IS_MOCK = False
IS_DRY = False
DESIGN_DIR = None  # design/ docs root for the Help browser


def init_routes(ccflet_app, mock=False, dry_run=False, design_dir=None):
    global ccflet, IS_MOCK, IS_DRY, DESIGN_DIR
    ccflet = ccflet_app
    IS_MOCK = mock
    IS_DRY = dry_run
    DESIGN_DIR = design_dir


def _user():
    hdr = request.headers.get("X-CCFlet-User")
    if hdr:
        try:
            return json.loads(hdr)
        except Exception:
            pass
    body = request.get_json(silent=True) or {}
    return body.get("user")


def _nodes_arg(default_all=True):
    data = request.get_json(silent=True) or {}
    nodes = data.get("nodes")
    if not nodes and default_all:
        nodes = ccflet.fleet.names()
    # keep only known nodes
    return [n for n in (nodes or []) if ccflet.fleet.get(n)]


# ============ pages ==========================================================
@web.route("/")
def dashboard():
    return render_template("dashboard.html", fleet=ccflet.fleet, mock=IS_MOCK, dry=IS_DRY)


@web.route("/node/<name>")
def node_page(name):
    node = ccflet.fleet.get(name)
    if not node:
        abort(404)
    roleA = ccflet.profiles.load("roleA")
    roleB = ccflet.profiles.load("roleB")
    # embed=1 → chromeless render (no header/breadcrumb) so the dashboard can
    # host this page inside a per-node tab (the "tabs" layout).
    embed = request.args.get("embed") in ("1", "true", "yes")
    return render_template("node.html", node=node, fleet=ccflet.fleet,
                           roleA=roleA, roleB=roleB, mock=IS_MOCK, dry=IS_DRY, embed=embed)


@web.route("/sessions")
def sessions_page():
    return render_template("sessions.html", sessions=ccflet.sessions.list_sessions(),
                           current=ccflet.current_id)


@web.route("/sessions/<sid>")
def session_view(sid):
    storage = ccflet.sessions.get_session(sid)
    if not storage:
        abort(404)
    manifest = storage.load_manifest()
    events = [e.to_dict() for e in EventStream(storage.events_path).get_all_events()]
    return render_template("session_view.html", manifest=manifest, events=events, sid=sid)


# ============ help / design docs =============================================
@web.route("/help")
def help_page():
    return render_template("help.html", mock=IS_MOCK, dry=IS_DRY)


@web.route("/api/design/tree")
def api_design_tree():
    # Read the folder fresh on every request → the tree reflects file
    # add/rename/remove with no restart (adjustable, not pre-fixed).
    return jsonify({"tree": build_tree(DESIGN_DIR), "root": bool(DESIGN_DIR)})


@web.route("/api/design/doc")
def api_design_doc():
    doc = read_doc(DESIGN_DIR, request.args.get("path", ""))
    if not doc:
        abort(404)
    return jsonify(doc)


# ============ config editor (D8 — config over code) ==========================
@web.route("/config")
def config_page():
    return render_template("config.html", mock=IS_MOCK, dry=IS_DRY)


@web.route("/api/config/tree")
def api_config_tree():
    # Read the roots fresh each request → add/rename/remove reflects with no restart.
    # The active profile + the list of profiles ride along so the Config page's profile
    # selector renders without a second round-trip (P8).
    return jsonify({"roots": ccflet.config.list_tree(), **ccflet.list_profiles()})


@web.route("/api/config/profiles")
def api_config_profiles():
    # The switchable editable-YAML sets: {"active": name, "profiles": [...]} (P8).
    return jsonify(ccflet.list_profiles())


@web.route("/api/config/profile", methods=["POST"])
def api_config_profile_switch():
    data = request.get_json(silent=True) or {}
    res = ccflet.switch_profile(data.get("name", ""), user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@web.route("/api/config/profile/new", methods=["POST"])
def api_config_profile_new():
    data = request.get_json(silent=True) or {}
    res = ccflet.create_profile(data.get("name", ""), data.get("from"), user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@web.route("/api/config/file")
def api_config_read():
    doc = ccflet.config.read_file(request.args.get("root", ""),
                                  request.args.get("path", ""))
    if not doc:
        abort(404)
    return jsonify(doc)


@web.route("/api/config/validate", methods=["POST"])
def api_config_validate():
    data = request.get_json(silent=True) or {}
    return jsonify(ccflet.config.validate(
        data.get("root", ""), data.get("path", ""), data.get("text", "")))


@web.route("/api/config/file", methods=["POST"])
def api_config_save():
    data = request.get_json(silent=True) or {}
    res = ccflet.save_config(data.get("root", ""), data.get("path", ""),
                             data.get("text", ""), user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@web.route("/api/config/revert", methods=["POST"])
def api_config_revert():
    data = request.get_json(silent=True) or {}
    res = ccflet.revert_config(data.get("root", ""), data.get("path", ""),
                               user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


# ============ fleet REST =====================================================
@web.route("/api/fleet")
def api_fleet():
    return jsonify({
        "name": ccflet.fleet.name,
        "default_variant": ccflet.fleet.default_variant,  # fallback for nodes that don't set one
        "algo": ccflet.fleet.algo,
        "mock": IS_MOCK,
        "dry_run": IS_DRY,
        "session": ccflet.current_id,
        "nodes": [
            {"name": n.name, "id": n.id, "host": n.host, "subnet": n.subnet,
             "variant": ccflet.fleet.node_variant(n)}     # per-node variant
            for n in ccflet.fleet.nodes
        ],
        "groups": ccflet.fleet.groups_as_list(),
    })


@web.route("/api/fleet/status")
def api_status():
    return jsonify({"statuses": ccflet.orch.snapshot(),
                    "default_variant": ccflet.fleet.default_variant})


# ============ status LEDs (the States bar under the header) ==================
@web.route("/api/states")
def api_states():
    """Current state-LED colors (ping links + cmd states) for the initial render;
    live updates then arrive over SocketIO (``states_status``)."""
    if not ccflet.state_monitor:
        return jsonify({"states": [], "poll_interval": 0})
    return jsonify({"states": ccflet.state_monitor.snapshot(),
                    "poll_interval": ccflet.states.poll_interval})


@web.route("/api/states/refresh", methods=["POST"])
def api_states_refresh():
    if ccflet.state_monitor:
        ccflet.run_bg(ccflet.state_monitor.poll_once)
    return jsonify({"success": True})


# ============ health gates (config-driven; the per-node gate cells, P8) =======
@web.route("/api/gates")
def api_gates():
    """The configured gate metas (key/label/kind/on/variants) so the dashboard builds its
    gate cells client-side — editing a ``gates/*.yaml`` + reload changes the cells with no
    template edit. The live colors arrive per node over ``node_status``/``gate_changed``."""
    gates = ccflet.gates
    return jsonify({"gates": gates.metas() if gates else []})


@web.route("/api/fleet/variant", methods=["POST"])
def api_set_variant():
    """Bulk: set **every** node to one variant. Not used by the UI (variant is
    per-node); kept for tests / any future bulk path."""
    data = request.get_json(silent=True) or {}
    variant = data.get("variant")
    try:
        ccflet.set_variant(variant, user=_user())
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    ccflet.run_bg(ccflet.orch.poll_all)
    return jsonify({"success": True, "variant": ccflet.fleet.default_variant})


@web.route("/api/node/<name>/variant", methods=["POST"])
def api_set_node_variant(name):
    """Toggle one node's variant (per-node)."""
    data = request.get_json(silent=True) or {}
    variant = data.get("variant")
    try:
        ccflet.set_node_variant(name, variant, user=_user())
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    ccflet.run_bg(lambda: ccflet.orch.poll_node(name))
    return jsonify({"success": True, "node": name, "variant": variant})


@web.route("/api/fleet/algo", methods=["POST"])
def api_set_algo():
    data = request.get_json(silent=True) or {}
    algo = (data.get("algo") or "").strip()
    if not algo:
        return jsonify({"success": False, "error": "no algo"}), 400
    try:
        ccflet.set_algo(algo, user=_user())
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "algo": ccflet.fleet.algo})


@web.route("/api/fleet/refresh", methods=["POST"])
def api_refresh():
    ccflet.run_bg(ccflet.orch.poll_all)
    return jsonify({"success": True})


@web.route("/api/fleet/bring_up", methods=["POST"])
def api_bring_up():
    nodes, user = _nodes_arg(), _user()
    ccflet.run_bg(ccflet.orch.bring_up_fleet, nodes, user)
    return jsonify({"success": True, "nodes": nodes, "started": True})


@web.route("/api/fleet/tear_down", methods=["POST"])
def api_tear_down():
    nodes, user = _nodes_arg(), _user()
    ccflet.run_bg(ccflet.orch.tear_down_fleet, nodes, user)
    return jsonify({"success": True, "nodes": nodes, "started": True})


@web.route("/api/fleet/deploy", methods=["POST"])
def api_deploy():
    data = request.get_json(silent=True) or {}
    nodes, user = _nodes_arg(), _user()
    build = bool(data.get("build", False))
    ccflet.run_bg(ccflet.orch.deploy_fleet, nodes, user, build)
    return jsonify({"success": True, "nodes": nodes, "started": True})


# ============ node REST ======================================================
@web.route("/api/node/<name>/status")
def api_node_status(name):
    if not ccflet.fleet.get(name):
        abort(404)
    ns = ccflet.orch.poll_node(name)
    return jsonify(ns.to_dict())


# ============ operator commands (D8 — config over code) ======================
@web.route("/api/commands")
def api_commands():
    return jsonify({
        "commands": ccflet.commands.metas() if ccflet.commands else [],
        "allow_local": ccflet.allow_local,
    })


@web.route("/api/node/<name>/command", methods=["POST"])
def api_node_command(name):
    if not ccflet.fleet.get(name):
        abort(404)
    cname = (request.get_json(silent=True) or {}).get("command")
    if not cname:
        return jsonify({"ok": False, "error": "no command"}), 400
    cmd = ccflet.commands.get(cname) if ccflet.commands else None
    if cmd is None:
        return jsonify({"ok": False, "error": f"unknown command {cname}"}), 400
    # the single-node endpoint runs only node-scoped commands — a fleet-scoped
    # command would silently fan out and mislabel the audit (run it from the fleet
    # command bar instead).
    if cmd.scope != "node":
        return jsonify({"ok": False,
                        "error": "this command is fleet-scoped; trigger it from the fleet command bar"}), 400
    res = ccflet.orch.run_custom(cname, node=name, user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@web.route("/api/fleet/command", methods=["POST"])
def api_fleet_command():
    cname = (request.get_json(silent=True) or {}).get("command")
    if not cname:
        return jsonify({"ok": False, "error": "no command"}), 400
    nodes = _nodes_arg(default_all=False) or None
    res = ccflet.orch.run_custom(cname, nodes=nodes, user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@web.route("/api/node/<name>/action", methods=["POST"])
def api_node_action(name):
    if not ccflet.fleet.get(name):
        abort(404)
    data = request.get_json(silent=True) or {}
    role = data.get("role", "roleA")
    action = data.get("action")
    if not action:
        return jsonify({"success": False, "error": "no action"}), 400
    res = ccflet.orch.run_action(name, role, action, user=_user())
    return jsonify(res.to_dict())


@web.route("/api/node/<name>/<seq>", methods=["POST"])
def api_node_sequence(name, seq):
    if not ccflet.fleet.get(name):
        abort(404)
    user = _user()
    if seq == "bring_up":
        ccflet.run_bg(ccflet.orch.bring_up, name, user)
    elif seq == "tear_down":
        ccflet.run_bg(ccflet.orch.tear_down, name, user)
    elif seq == "deploy":
        build = bool((request.get_json(silent=True) or {}).get("build", False))
        ccflet.run_bg(lambda: ccflet.orch.deploy(name, build=build, user=user))
    else:
        return jsonify({"success": False, "error": f"unknown sequence {seq}"}), 400
    return jsonify({"success": True, "started": True})


# ============ Logs view (base-station log windows, D8 — config over code) =====
@web.route("/api/logs")
def api_logs():
    """The configured log windows for the Logs view (built client-side, so editing
    ``logs/logs.yaml`` + reload changes the view with no template edit). ``enabled``
    reflects whether real base-station tailing is available (false → simulated/disabled
    panes)."""
    if not ccflet.logs:
        return jsonify({"windows": [], "enabled": False})
    enabled = ccflet.allow_local and not (IS_MOCK or IS_DRY)
    return jsonify({"windows": ccflet.logs.metas(), "enabled": enabled})


@web.route("/api/node/<name>/logs")
def api_node_logs(name):
    if not ccflet.fleet.get(name):
        abort(404)
    roleA = ccflet.profiles.load("roleA")
    roleB = ccflet.profiles.load("roleB")
    logs = list((roleA.logs if roleA else {}).keys())
    if ccflet.fleet.node_variant(name) == "B" and roleB:
        logs += list(roleB.logs.keys())
    return jsonify({"logs": logs})


# ============ session REST ===================================================
@web.route("/api/sessions")
def api_sessions():
    return jsonify({"sessions": ccflet.sessions.list_sessions(), "current": ccflet.current_id})


@web.route("/api/sessions", methods=["POST"])
def api_new_session():
    name = (request.get_json(silent=True) or {}).get("name")
    sid = ccflet.start_session(name, user=_user())
    return jsonify({"success": True, "session": sid})


@web.route("/api/sessions/close", methods=["POST"])
def api_close_session():
    # closes the live (current) session — used by the dock's close button
    return jsonify({"success": ccflet.close_session(user=_user())})


@web.route("/api/sessions/<sid>/close", methods=["POST"])
def api_close_session_by_id(sid):
    if not ccflet.sessions.get_session(sid):
        abort(404)
    ok = ccflet.close_session(sid, user=_user())
    return jsonify({"success": ok}), (200 if ok else 400)


@web.route("/api/sessions/<sid>/rename", methods=["POST"])
def api_rename_session(sid):
    if not ccflet.sessions.get_session(sid):
        abort(404)
    name = (request.get_json(silent=True) or {}).get("name", "")
    res = ccflet.rename_session(sid, name, user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@web.route("/api/session/note", methods=["POST"])
def api_note():
    note = (request.get_json(silent=True) or {}).get("note", "").strip()
    if not note:
        return jsonify({"success": False, "error": "empty note"}), 400
    ccflet.note(note, user=_user())
    return jsonify({"success": True})


@web.route("/api/sessions/<sid>/events")
def api_session_events(sid):
    storage = ccflet.sessions.get_session(sid)
    if not storage:
        abort(404)
    after = int(request.args.get("after", 0))
    events = [e.to_dict() for e in EventStream(storage.events_path).iter_events(after)]
    return jsonify({"events": events})


@web.route("/sessions/<sid>/export")
def api_export(sid):
    storage = ccflet.sessions.get_session(sid)
    if not storage:
        abort(404)
    # snapshot every configured log window into artifacts/logs/ so the ZIP always carries
    # the operator-defined base-station logs (whether or not a pane was opened live).
    ccflet.capture_log_artifacts(storage)
    archive = storage.create_archive()
    return send_file(archive, mimetype="application/zip", as_attachment=True,
                     download_name=os.path.basename(archive))


@web.route("/api/sessions/<sid>", methods=["DELETE"])
def api_delete_session(sid):
    if sid == ccflet.current_id:
        return jsonify({"success": False, "error": "cannot delete active session"}), 400
    return jsonify({"success": ccflet.sessions.delete_session(sid)})
