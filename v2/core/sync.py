"""
Real-time multi-operator synchronization for ccflet (Flask-SocketIO).

Fleet-first: every operator joins one shared `fleet` room on connect, so all of
them see the same dashboard, the same audit timeline and the same live log
streams. Log streams additionally use per-(node,log) rooms so a browser only
receives the tails it opened.
"""

from datetime import datetime, timezone
from typing import Dict, Set, Optional
from dataclasses import dataclass
from flask_socketio import SocketIO, emit, join_room
from flask import request

FLEET_ROOM = "fleet"


def stream_room(node: str, log: str) -> str:
    return f"stream:{node}:{log}"


@dataclass
class ConnectedUser:
    sid: str
    username: str
    color: str
    connected_at: str


class SyncManager:
    """Tracks connected operators and broadcasts fleet-wide updates."""

    USER_COLORS = [
        "#58a6ff", "#3fb950", "#f85149", "#d29922",
        "#a371f7", "#f778ba", "#79c0ff", "#7ee787",
    ]

    def __init__(self, socketio: SocketIO):
        self.socketio = socketio
        self.users: Dict[str, ConnectedUser] = {}
        self._color_index = 0
        self._disconnect_cbs = []  # called with (sid) on client disconnect
        self._register_events()

    def on_disconnect(self, cb):
        """Register a callback(sid) invoked when a client disconnects."""
        self._disconnect_cbs.append(cb)

    def _next_color(self) -> str:
        color = self.USER_COLORS[self._color_index % len(self.USER_COLORS)]
        self._color_index += 1
        return color

    def _register_events(self):
        @self.socketio.on("connect")
        def handle_connect():
            sid = request.sid
            user = ConnectedUser(
                sid=sid,
                username=f"op-{sid[:5]}",
                color=self._next_color(),
                connected_at=datetime.now(timezone.utc).isoformat(),
            )
            self.users[sid] = user
            join_room(FLEET_ROOM, sid=sid)
            emit("user_info", {"sid": sid, "username": user.username, "color": user.color})
            self._broadcast_roster()

        @self.socketio.on("disconnect")
        def handle_disconnect():
            sid = request.sid
            for cb in self._disconnect_cbs:
                try:
                    cb(sid)
                except Exception:
                    pass
            if sid in self.users:
                del self.users[sid]
                self._broadcast_roster()

        @self.socketio.on("set_username")
        def handle_set_username(data):
            sid = request.sid
            user = self.users.get(sid)
            if user:
                user.username = (data.get("username") or user.username)[:32]
                self._broadcast_roster()

    def roster(self) -> list:
        return [
            {"sid": u.sid, "username": u.username, "color": u.color}
            for u in self.users.values()
        ]

    def _broadcast_roster(self):
        self.socketio.emit("roster", {"users": self.roster()}, room=FLEET_ROOM)

    def user_by_sid(self, sid: str) -> Optional[ConnectedUser]:
        return self.users.get(sid)

    # ---- fleet-wide broadcasts -------------------------------------------
    def broadcast_event(self, event: dict):
        self.socketio.emit("new_event", event, room=FLEET_ROOM)

    def broadcast_node_status(self, status: dict):
        self.socketio.emit("node_status", status, room=FLEET_ROOM)

    def broadcast_gate(self, node: str, gates: dict):
        self.socketio.emit("gate_changed", {"node": node, "gates": gates}, room=FLEET_ROOM)

    def broadcast_fleet_meta(self, variant: str, algo: str):
        self.socketio.emit("fleet_meta", {"variant": variant, "algo": algo}, room=FLEET_ROOM)

    def broadcast_node_variant(self, node: str, variant: str):
        """One node's variant was toggled (per-node): dashboards update just that
        card's A/B state + variant-B-only metric visibility."""
        self.socketio.emit("node_variant", {"node": node, "variant": variant}, room=FLEET_ROOM)

    def broadcast_fleet_changed(self, nodes: list, variant: str):
        """Fleet inventory edited from the Config page: the node set may have changed,
        so dashboards should prompt a reload (their grid is server-rendered)."""
        self.socketio.emit("fleet_changed", {"nodes": nodes, "variant": variant}, room=FLEET_ROOM)

    def broadcast_commands_changed(self):
        """Command catalog edited (D8): clients rebuild their custom-command buttons."""
        self.socketio.emit("commands_changed", {}, room=FLEET_ROOM)

    def broadcast_net_status(self, links: list):
        """Base-station connectivity poll result → the top-bar LEDs (the configured
        off-fleet links). ``links`` is a list of ``{key,label,host,hint,up}``."""
        self.socketio.emit("net_status", {"links": links}, room=FLEET_ROOM)

    def broadcast_log_line(self, node: str, log: str, line: str):
        self.socketio.emit(
            "log_line", {"node": node, "log": log, "line": line},
            room=stream_room(node, log),
        )

    def broadcast_action(self, node: str, action: str, state: str, user: Optional[dict]):
        self.socketio.emit(
            "action_progress",
            {"node": node, "action": action, "state": state, "user": user},
            room=FLEET_ROOM,
        )


# Global instance (set by app.py)
sync_manager: Optional[SyncManager] = None


def init_sync(socketio: SocketIO) -> SyncManager:
    global sync_manager
    sync_manager = SyncManager(socketio)
    return sync_manager


def get_sync_manager() -> Optional[SyncManager]:
    return sync_manager
