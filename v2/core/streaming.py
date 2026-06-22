"""
Live log streaming for ccflet.

For each requested (node, logname) the StreamManager opens
`tail -n 100 -F <path>` on a **dedicated** channel via the client's
`exec_stream()` (never through the lock-serialized `execute()`), and pushes each
line to the per-(node,log) SocketIO room as a `log_line` event — rendered into an
xterm.js pane in the browser. Lines are also persisted into the session's
logs/ dir for the audit ZIP. Tail lifecycle is tied to client subscribe/
unsubscribe (and disconnect); the number of concurrent tails is capped.
"""

import threading
from typing import Dict, Optional, Set

from flask_socketio import SocketIO, join_room, leave_room
from flask import request

from . import profiles as P
from .events import EventType
from .sync import stream_room

MAX_TAILS = 24
TAIL_PREFIX = "tail -n 100 -F"


class _Tail:
    def __init__(self, node: str, log: str):
        self.node = node
        self.log = log
        self.stop = threading.Event()
        self.subscribers: Set[str] = set()
        self.thread: Optional[threading.Thread] = None


class StreamManager:
    def __init__(self, socketio: SocketIO, orchestrator, sync_manager,
                 session_getter=None):
        self.socketio = socketio
        self.orch = orchestrator
        self.sync = sync_manager
        self.session_getter = session_getter  # callable -> SessionStorage|None
        self._tails: Dict[tuple, _Tail] = {}
        self._lock = threading.Lock()
        self._register()
        if sync_manager:
            sync_manager.on_disconnect(self._on_disconnect)

    # ---- socket handlers -------------------------------------------------
    def _register(self):
        @self.socketio.on("subscribe_log")
        def _subscribe(data):
            node, log = data.get("node"), data.get("log")
            if node and log:
                join_room(stream_room(node, log), sid=request.sid)
                self.start(node, log, request.sid)

        @self.socketio.on("unsubscribe_log")
        def _unsubscribe(data):
            node, log = data.get("node"), data.get("log")
            if node and log:
                leave_room(stream_room(node, log), sid=request.sid)
                self.stop(node, log, request.sid)

    def _on_disconnect(self, sid: str):
        with self._lock:
            keys = list(self._tails.keys())
        for node, log in keys:
            self.stop(node, log, sid)

    # ---- resolve log → (role, command) ----------------------------------
    def _resolve(self, node: str, log: str):
        node_obj = self.orch.fleet.get(node)
        if not node_obj:
            return None, None
        params = self.orch.fleet.params(node_obj)
        for role in ("roleA", "roleB"):
            prof = self.orch.profiles.get(role)
            if prof and log in prof.logs:
                path = P.substitute(prof.logs[log], params)
                return role, f"{TAIL_PREFIX} {path}"
        return None, None

    # ---- lifecycle -------------------------------------------------------
    def start(self, node: str, log: str, sid: str):
        key = (node, log)
        with self._lock:
            tail = self._tails.get(key)
            if tail:
                tail.subscribers.add(sid)
                return
            if len(self._tails) >= MAX_TAILS:
                self.socketio.emit("log_line", {
                    "node": node, "log": log,
                    "line": "[ccflet] too many active streams; close one and retry"},
                    room=stream_room(node, log))
                return
            tail = _Tail(node, log)
            tail.subscribers.add(sid)
            self._tails[key] = tail
        role, command = self._resolve(node, log)
        if not command:
            self.socketio.emit("log_line", {
                "node": node, "log": log, "line": f"[ccflet] unknown log '{log}'"},
                room=stream_room(node, log))
            return
        tail.thread = threading.Thread(
            target=self._run, args=(tail, role, command), daemon=True)
        tail.thread.start()
        if self.orch.events:
            self.orch.events.append(EventType.STREAM_STARTED, {"node": node, "log": log})

    def _run(self, tail: _Tail, role: str, command: str):
        client = self.orch.pool.get(tail.node, role)
        room = stream_room(tail.node, tail.log)
        storage = self.session_getter() if self.session_getter else None
        try:
            for line in client.exec_stream(command, stop_event=tail.stop):
                if tail.stop.is_set():
                    break
                self.socketio.emit(
                    "log_line", {"node": tail.node, "log": tail.log, "line": line},
                    room=room)
                if storage:
                    try:
                        storage.append_log(f"{tail.node}-{tail.log}", line)
                    except Exception:
                        pass
        except Exception as e:  # noqa: BLE001
            self.socketio.emit("log_line", {
                "node": tail.node, "log": tail.log, "line": f"[ccflet] stream error: {e}"},
                room=room)

    def stop(self, node: str, log: str, sid: str):
        key = (node, log)
        with self._lock:
            tail = self._tails.get(key)
            if not tail:
                return
            tail.subscribers.discard(sid)
            if tail.subscribers:
                return
            tail.stop.set()
            del self._tails[key]
        if self.orch.events:
            self.orch.events.append(EventType.STREAM_STOPPED, {"node": node, "log": log})

    def stop_all(self):
        with self._lock:
            tails = list(self._tails.values())
            self._tails.clear()
        for t in tails:
            t.stop.set()
