#!/usr/bin/env python3
"""
ccflet — SSH Command & Control for a generic node fleet.

A base-station web app that brings a fleet of remote nodes **up**, **watches** it,
and brings it **down** over SSH — with everything recorded. Each node runs one of
two per-node variants (A/B). This module is the composition root: it loads the
fleet + profiles, builds the orchestrator (real paramiko or the mock backend),
wires Flask + SocketIO, and exposes a thin CCFletApp facade to the routes.

  python app.py [--host H | --public] [--port P] [--fleet fleet.yaml]
                [--profiles-dir D] [--runs-dir D] [--mock] [--dry-run] [--no-poll]

SocketIO runs in threading async_mode (with simple-websocket for native WS) — no
gevent monkey-patching, which keeps paramiko's own threads clean.
"""

import argparse
import os
import socket
import sys
import threading

import yaml
from flask import Flask
from flask_socketio import SocketIO

from core import (
    Fleet, load_fleet, ProfileManager, SessionManager, SessionManifest,
    EventStream, EventType, Orchestrator, StreamManager, init_sync, now_iso,
    render_connection, CommandCatalog, ConfigStore, default_roots,
    StateRegistry, StateMonitor, GateRegistry, LogsRegistry, LogStreamManager,
)
from core.mock_ssh import MockFleetState, MockSSHClient


def resource(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


# UDP "connect" to this address only does a route lookup (no packets are sent), so
# any routable placeholder works; RFC 5737 TEST-NET-3, never a real destination.
_ROUTE_PROBE_ADDR = "203.0.113.1"


class CCFletApp:
    """Facade tying the fleet, orchestrator, sessions and live sync together."""

    def __init__(self, fleet: Fleet, profile_mgr, session_mgr, orchestrator,
                 sync_manager, socketio, mock_state=None):
        self.fleet = fleet
        self.profiles = profile_mgr
        self.sessions = session_mgr
        self.orch = orchestrator
        self.sync = sync_manager
        self.socketio = socketio
        self.mock_state = mock_state
        self.current_id = None
        self.current_storage = None
        self.mock = False
        self.dry_run = False
        # config-over-code (D8): set by create_app
        self.fleet_path = None
        self.config = None      # ConfigStore (Config page backend)
        self.commands = None    # CommandCatalog (operator command catalog)
        self.allow_local = True
        # base-station status LEDs (the States bar): set by create_app
        self.states_dir = None
        self.states = None          # StateRegistry (ping links + cmd states → the bar)
        self.state_monitor = None   # StateMonitor (poller → states_status broadcast)
        # config-driven health gates (the per-node gate cells): set by create_app
        self.gates_dir = None
        self.gates = None           # GateRegistry (gates/*.yaml; held by the orchestrator)
        # base-station log windows (the Logs view): set by create_app
        self.logs_dir = None
        self.logs = None            # LogsRegistry (the configured log windows)
        self.log_stream = None      # LogStreamManager (live local-file tailing)

    # ---- sessions --------------------------------------------------------
    def start_session(self, name=None, user=None):
        sid = self.sessions.generate_session_id(name)
        manifest = SessionManifest(
            session_id=sid, name=name or sid, status="open", created_at=now_iso(),
            variant=self.fleet.default_variant, algo=self.fleet.algo,
            node_names=self.fleet.names(),
        )
        storage = self.sessions.create_session(manifest, self.fleet.to_yaml())
        self.current_id = sid
        self.current_storage = storage
        self.orch.set_event_stream(EventStream(storage.events_path))
        self.orch._emit(EventType.SESSION_STARTED, {"session": sid, "name": manifest.name,
                        "variant": self.fleet.default_variant, "algo": self.fleet.algo},
                        user=user)
        return sid

    def close_session(self, sid=None, user=None):
        """Close (mark done) a session by id. ``sid`` None or the current id closes
        the live session through the live event stream (seq-safe); any other id is a
        stale 'open' session closed in place and audited in its own log."""
        if sid is None or sid == self.current_id:
            if not self.current_storage:
                return False
            self.orch._emit(EventType.SESSION_CLOSED, {"session": self.current_id}, user=user)
            m = self.current_storage.load_manifest()
            if m:
                m.status = "closed"
                m.closed_at = now_iso()
                self.current_storage.save_manifest(m)
            return True
        storage = self.sessions.get_session(sid)
        if not storage:
            return False
        m = storage.load_manifest()
        if not m or m.status == "closed":
            return False
        m.status = "closed"
        m.closed_at = now_iso()
        storage.save_manifest(m)
        ev = EventStream(storage.events_path).append(
            EventType.SESSION_CLOSED, {"session": sid}, user=user)
        if self.sync:
            self.sync.broadcast_event(ev.to_dict())
        return True

    def rename_session(self, sid, name, user=None):
        """Rename a session's display label (the session id / path never changes).
        Audited like any action (D6): in the live stream if it's the current
        session, otherwise appended to the renamed session's own log."""
        name = (name or "").strip()[:80]
        if not name:
            return {"ok": False, "error": "empty name"}
        storage = self.sessions.get_session(sid)
        if not storage:
            return {"ok": False, "error": "unknown session"}
        m = storage.load_manifest()
        if not m:
            return {"ok": False, "error": "no manifest"}
        old, m.name = m.name, name
        storage.save_manifest(m)
        data = {"session": sid, "name": name, "old": old, "text": f"rename: {old} → {name}"}
        if sid == self.current_id:
            self.orch._emit(EventType.SESSION_RENAMED, data, user=user)
        else:
            ev = EventStream(storage.events_path).append(
                EventType.SESSION_RENAMED, data, user=user)
            if self.sync:
                self.sync.broadcast_event(ev.to_dict())
        return {"ok": True, "session": sid, "name": name}

    def _update_manifest(self, **fields):
        if not self.current_storage:
            return
        m = self.current_storage.load_manifest()
        if not m:
            return
        for k, v in fields.items():
            setattr(m, k, v)
        self.current_storage.save_manifest(m)

    # ---- runtime selection ----------------------------------------------
    def set_node_variant(self, name, variant, user=None):
        """Toggle one node's variant (per-node). Validates + audits + pushes a
        `node_variant` so the node's card updates in place."""
        self.fleet.set_node_variant(name, variant)  # raises on bad variant / unknown node
        self.orch._emit(EventType.NOTE, {"text": f"{name} variant → {variant}"}, user=user)
        if self.sync:
            self.sync.broadcast_node_variant(name, variant)

    def set_variant(self, variant, user=None):
        """Bulk helper — set **every** node to `variant`. Not exposed in the UI
        (variant is per-node) but kept for the /api/fleet/variant endpoint, tests,
        and future bulk."""
        self.fleet.set_variant(variant)
        self._update_manifest(variant=variant)
        self.orch._emit(EventType.NOTE, {"text": f"all nodes variant → {variant}"}, user=user)
        if self.sync:
            for n in self.fleet.names():
                self.sync.broadcast_node_variant(n, variant)

    def set_algo(self, algo, user=None):
        self.fleet.set_algo(algo)
        self._update_manifest(algo=algo)
        self.orch._emit(EventType.NOTE, {"text": f"fleet algo → {algo}"}, user=user)
        if self.sync:
            self.sync.broadcast_fleet_meta(self.fleet.default_variant, self.fleet.algo)

    def note(self, text, user=None):
        self.orch._emit(EventType.NOTE, {"text": text}, user=user)

    # ---- config over code (D8) ------------------------------------------
    def save_config(self, root_key, relpath, text, user=None):
        """Validate + back up + write a config file, then hot-reload its scope.

        Returns the ConfigStore result dict (``ok``/``error``/``line``/…); on
        success it also carries the ``reloaded`` scope summary. The whole thing is
        audited — `CONFIG_SAVED` on a good write (the safety net, D6)."""
        if not self.config:
            return {"ok": False, "error": "config store not available"}
        res = self.config.write_file(root_key, relpath, text)
        if not res.get("ok"):
            return res
        self.orch._emit(EventType.CONFIG_SAVED,
                        {"root": root_key, "path": res.get("path", relpath),
                         "backup": res.get("backup")}, user=user)
        res.update(self._reload_after_write(root_key, user))
        return res

    def revert_config(self, root_key, relpath, user=None):
        """Restore the newest backup of a config file, then hot-reload its scope."""
        if not self.config:
            return {"ok": False, "error": "config store not available"}
        res = self.config.revert(root_key, relpath)
        if not res.get("ok"):
            return res
        self.orch._emit(EventType.CONFIG_SAVED,
                        {"root": root_key, "path": res.get("path", relpath),
                         "reverted": True}, user=user)
        res.update(self._reload_after_write(root_key, user))
        return res

    def _reload_after_write(self, root_key, user):
        """Hot-reload the saved scope, but never let a reload error become a 500 —
        the file is already validated + on disk; report a reload problem cleanly."""
        try:
            return {"reloaded": self.reload_config(self.config.scope_of(root_key), user=user)}
        except Exception as e:  # noqa: BLE001
            self.orch._emit(EventType.ERROR,
                            {"where": "reload_config", "root": root_key, "error": str(e)},
                            user=user)
            return {"reloaded": None, "reload_error": str(e)}

    def reload_config(self, scope, user=None):
        """Hot-reload one config scope in place — no restart (D8).

        scope ∈ {fleet, profiles, commands, states, gates, logs}. Returns a short summary.
        Every holder (orchestrator, client factory, mock state) shares the same `Fleet`
        object, so the fleet is reloaded in place; the connection pool is closed so
        changed hosts reconnect lazily.
        """
        if scope == "fleet":
            with open(self.fleet_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            self.fleet.reload_from_dict(raw, source=self.fleet_path)
            self.orch.sync_node_locks()
            if self.mock_state:
                self.mock_state.reload(self.fleet)
            self.orch.pool.close_all()
            self._update_manifest(variant=self.fleet.default_variant, algo=self.fleet.algo,
                                  node_names=self.fleet.names())
            if self.sync:
                # node set / per-node variants may have changed; dashboards re-render
                # their server-rendered grid (which reflects each node's current variant).
                self.sync.broadcast_fleet_changed(self.fleet.names(), self.fleet.default_variant)
            summary = f"fleet · {len(self.fleet.nodes)} nodes"
        elif scope == "profiles":
            self.profiles.invalidate()
            self.orch.reload_profiles(self.profiles)
            self.orch.pool.close_all()
            summary = "profiles"
        elif scope == "commands":
            if self.commands:
                self.commands.reload()
            if self.sync:
                self.sync.broadcast_commands_changed()
            summary = "commands"
        elif scope == "states":
            # Reload the indicator list in place from the states dir (the monitor holds
            # the same ref), then kick an immediate check so the LEDs reflect the edit.
            self.states.reload()
            if self.state_monitor:
                self.run_bg(self.state_monitor.poll_once)
            summary = f"states · {len(self.states.indicators)} indicators"
        elif scope == "gates":
            # Reload the gate registry in place (the orchestrator holds the same ref),
            # drop the cadence cache, re-poll so cells reflect the edit, and tell open
            # dashboards to rebuild their gate cells from the fresh /api/gates.
            self.orch.reload_gates()
            self.run_bg(self.orch.poll_all)
            if self.sync:
                self.sync.broadcast_gates_changed()
            summary = f"gates · {len(self.gates.specs)} gates"
        elif scope == "logs":
            # Reload the window list in place (the streamer holds the same registry ref),
            # then tell the Logs view to rebuild its panes from the fresh /api/logs.
            self.logs.reload()
            if self.sync:
                self.sync.broadcast_logs_changed()
            summary = f"logs · {len(self.logs.windows)} windows"
        else:
            return None
        self.orch._emit(EventType.CONFIG_RELOADED,
                        {"scope": scope, "summary": summary}, user=user)
        return summary

    # ---- log artifacts (session ZIP) ------------------------------------
    def capture_log_artifacts(self, storage):
        """Snapshot every configured log window into the session's ``artifacts/logs/``
        so the exported ZIP always carries the logs the operator defined — whether or
        not a pane was opened live (P6, P8). Echo-only under ``--mock``/``--dry-run`` and
        skipped per-file when base-station local exec is disabled. Audited as a note."""
        if not self.logs or not storage:
            return []
        from core import snapshot_windows
        written = snapshot_windows(self.logs.windows, storage,
                                   simulate=(self.mock or self.dry_run),
                                   allow_local=self.allow_local)
        ok = sum(1 for w in written if w.get("ok"))
        # Audit into the *exported* session's own log (export may target a past session),
        # so the captured artifacts are recorded alongside the run they ship with (P6).
        try:
            EventStream(storage.events_path).append(
                EventType.NOTE,
                {"text": f"captured {ok}/{len(written)} log artifact(s) on export"})
        except Exception:  # noqa: BLE001 — a stale/closed log must not block the download
            pass
        return written

    # ---- background runner ----------------------------------------------
    def run_bg(self, fn, *args, **kwargs):
        self.socketio.start_background_task(fn, *args, **kwargs)


def _real_factory(fleet, profile_mgr):
    """Build a real paramiko client per (node, role); roleB uses the jump-host."""
    from core.ssh_client import SSHClientWrapper, ConnectionConfig

    def factory(node_name, role):
        node = fleet.get(node_name)
        prof = profile_mgr.load(role)
        params = fleet.params(node)
        conn = render_connection(prof.connection, params)
        cfg = ConnectionConfig.from_profile_connection(conn)
        return SSHClientWrapper(cfg)

    return factory


def create_app(fleet_path=None, profiles_dir=None, commands_dir=None, runs_dir=None,
               states_dir=None, logs_dir=None, gates_dir=None, mock=False, dry_run=False,
               poll=True, allow_local=True):
    here = os.path.dirname(os.path.abspath(__file__))
    fleet_path = fleet_path or os.path.join(here, "fleet", "fleet.yaml")
    profiles_dir = profiles_dir or os.path.join(here, "profiles")
    commands_dir = commands_dir or os.path.join(here, "commands")
    runs_dir = runs_dir or os.path.join(here, "runs")
    # the States config root: the dir holding the state-source files (ping + cmd). Kept
    # as networks/ on disk; surfaced as "States" on the Config page.
    states_dir = states_dir or os.path.join(here, "networks")
    # the Logs config root: the dir holding the log-window source files (base-station
    # tails). Surfaced as "Logs" on the Config page; the dashboard's third view.
    logs_dir = logs_dir or os.path.join(here, "logs")
    # the Gates config root: the dir holding one health gate per file (gates/*.yaml).
    # Surfaced as "Gates" on the Config page; drives the per-node gate cells (P8).
    gates_dir = gates_dir or os.path.join(here, "gates")

    fleet = load_fleet(fleet_path)
    profile_mgr = ProfileManager(profiles_dir)
    if profile_mgr.load("roleA") is None:
        raise SystemExit(f"missing roleA profile in {profiles_dir}")
    session_mgr = SessionManager(runs_dir)
    commands = CommandCatalog(commands_dir)   # loads commands_{host,roleA,roleB}.yaml
    states = StateRegistry(states_dir)        # loads networks.yaml (ping) + stateA.yaml (cmd)
    gates = GateRegistry(gates_dir)           # loads gates/*.yaml (config-driven gates)
    logs = LogsRegistry(logs_dir)             # loads logs.yaml (base-station log windows)
    config_store = ConfigStore(
        default_roots(fleet_path, profiles_dir, commands_dir, states_dir, logs_dir,
                      gates_dir=gates_dir),
        dry_run=dry_run)

    app = Flask(__name__,
                template_folder=resource(os.path.join("web", "templates")),
                static_folder=resource(os.path.join("web", "static")))
    app.config["SECRET_KEY"] = os.urandom(24)
    # cap request bodies — config saves carry whole files; nothing legitimate is big.
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB

    # operator-facing labels (the spec-derived domain identity) are available to
    # every template as `identity`; the load-bearing brand *tokens* stay in code.
    from domain.identity import IDENTITY

    @app.context_processor
    def _inject_identity():
        return {"identity": IDENTITY}
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    sync = init_sync(socketio)

    mock_state = None
    if mock:
        mock_state = MockFleetState(fleet)
        factory = lambda n, r: MockSSHClient(mock_state, n, r)  # noqa: E731
    else:
        factory = _real_factory(fleet, profile_mgr)

    orch = Orchestrator(fleet, profile_mgr, factory, sync_manager=sync, dry_run=dry_run,
                        gates=gates, allow_local=allow_local)
    orch.configure_commands(commands, commands_dir, allow_local=allow_local,
                            runs_dir=runs_dir, mock=mock)
    # base-station status LEDs: simulated (healthy) under mock/dry-run, exactly like
    # local commands echo instead of touching the network or the base station. cmd
    # states additionally stay neutral when local exec is disabled (they run shell here).
    def _log_state_change(state, old_color):
        # a States-bar LED flipped color → drop a compact line in the live session log
        # (audit, P6). The first poll is the baseline and emits nothing; only genuine
        # transitions land here. Under --mock the bar is steady-green, so this is quiet.
        orch._emit(EventType.STATE_CHANGED, {
            "key": state.get("key"), "label": state.get("label"),
            "kind": state.get("kind"), "from": old_color,
            "to": state.get("color"), "detail": state.get("detail", ""),
        })

    state_monitor = StateMonitor(states, sync_manager=sync, simulate=(mock or dry_run),
                                 allow_local=allow_local, on_change=_log_state_change)
    ccflet = CCFletApp(fleet, profile_mgr, session_mgr, orch, sync, socketio, mock_state)
    ccflet.mock = mock
    ccflet.dry_run = dry_run
    ccflet.fleet_path = fleet_path
    ccflet.config = config_store
    ccflet.commands = commands
    ccflet.allow_local = allow_local
    ccflet.states_dir = states_dir
    ccflet.states = states
    ccflet.state_monitor = state_monitor
    ccflet.gates_dir = gates_dir
    ccflet.gates = gates
    ccflet.logs_dir = logs_dir
    ccflet.logs = logs
    ccflet.start_session("boot")
    ccflet.stream_mgr = StreamManager(socketio, orch, sync,
                                  session_getter=lambda: ccflet.current_storage)
    # base-station log windows (the Logs view): live local-file tailing. Simulated under
    # mock/dry-run and gated by --no-local-commands, like local custom commands + cmd states.
    ccflet.log_stream = LogStreamManager(
        socketio, logs_getter=lambda: ccflet.logs,
        session_getter=lambda: ccflet.current_storage,
        events_getter=lambda: ccflet.orch.events,
        simulate=(mock or dry_run), allow_local=allow_local, sync_manager=sync)

    from web.routes import web as web_bp, init_routes
    init_routes(ccflet, mock=mock, dry_run=dry_run,
                design_dir=resource("design"))
    app.register_blueprint(web_bp)
    app.ccflet = ccflet

    if poll:
        orch.start_polling()
        state_monitor.start()
    return app, socketio, ccflet


def _lan_ip():
    """Best-effort primary LAN IP (the address other devices reach us on).

    Opens a UDP socket toward a public address to learn which local interface
    the OS would route through — no packets are actually sent. Returns None if
    it can't be determined (e.g. no network)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((_ROUTE_PROBE_ADDR, 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main():
    p = argparse.ArgumentParser(description="ccflet — Fleet Command & Control")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind host (default 127.0.0.1; use 0.0.0.0 for LAN)")
    p.add_argument("--public", action="store_true",
                   help="bind to 0.0.0.0 — reachable from anywhere on the local "
                        "network (overrides --host; closed-LAN posture, no auth)")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--fleet", help="fleet inventory YAML")
    p.add_argument("--profiles-dir", help="role profiles dir")
    p.add_argument("--commands-dir", help="operator command catalog dir")
    p.add_argument("--states-dir", help="status-LED state sources dir (the States bar)")
    p.add_argument("--gates-dir", help="health-gate sources dir (the per-node gate cells)")
    p.add_argument("--logs-dir", help="log-window sources dir (the Logs view)")
    p.add_argument("--runs-dir", help="ops-session storage dir")
    p.add_argument("--mock", action="store_true",
                   help="use the simulated fleet backend (no hardware)")
    p.add_argument("--dry-run", action="store_true",
                   help="print remote commands instead of running them")
    p.add_argument("--no-poll", action="store_true", help="do not auto-poll health")
    p.add_argument("--no-local-commands", action="store_true",
                   help="disable base-station (local) custom commands (D8)")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()

    app, socketio, ccflet = create_app(
        fleet_path=args.fleet, profiles_dir=args.profiles_dir,
        commands_dir=args.commands_dir, runs_dir=args.runs_dir,
        states_dir=args.states_dir, logs_dir=args.logs_dir, gates_dir=args.gates_dir,
        mock=args.mock, dry_run=args.dry_run, poll=not args.no_poll,
        allow_local=not args.no_local_commands)

    bind_host = "0.0.0.0" if args.public else args.host

    run_label = "MOCK" if args.mock else ("DRY-RUN" if args.dry_run else "LIVE")
    if args.public:
        lan = _lan_ip()
        lan_url = f"http://{lan}:{args.port}" if lan else "(LAN IP unavailable)"
        web_line = (f"web   : http://localhost:{args.port}\n"
                    f"  LAN   : {lan_url}   ← reachable from any device on this network")
    else:
        web_line = f"web   : http://{args.host}:{args.port}"
    print(f"""
  ccFleet — Command & Control  [{run_label}]
  fleet : {ccflet.fleet.name}  ({len(ccflet.fleet.nodes)} nodes, variant {ccflet.fleet.default_variant} per-node)
  {web_line}
  runs  : {ccflet.sessions.runs_dir}
  Ctrl+C to stop
""")
    try:
        socketio.run(app, host=bind_host, port=args.port, debug=args.debug,
                     allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        ccflet.orch.stop_polling()
        if ccflet.state_monitor:
            ccflet.state_monitor.stop()
        ccflet.stream_mgr.stop_all()
        if ccflet.log_stream:
            ccflet.log_stream.stop_all()
        ccflet.orch.pool.close_all()
        sys.exit(0)


if __name__ == "__main__":
    main()
