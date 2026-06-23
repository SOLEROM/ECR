"""
Live local-log streaming + ZIP-artifact capture for ccflet's **Logs** view.

The Logs view (dashboard third view) shows one live pane per configured
:class:`~core.logs.LogWindow`. Each pane tails a file **on the base station** (the
machine running ccFleet) — the read-only, many-panes counterpart of the per-node "Live
logs" tab in ``core/streaming.py`` (which tails fleet-node files over SSH). Because the
target is local, this uses a ``tail -F`` **subprocess** rather than the SSH pool.

Two surfaces, both here so the base-station file I/O stays in one shell:

  - :class:`LogStreamManager` — SocketIO ``subscribe_logwin`` / ``unsubscribe_logwin``
    handlers; for each subscribed window it runs ``tail -n <lines> -F <path>`` and pushes
    every line to the per-window room as a ``logwin_line`` event (rendered into an
    xterm.js pane). Lines are also persisted into the session ``logs/`` dir for the audit
    ZIP. Lifecycle is ref-counted per window (start on first subscriber, stop + kill the
    tail on the last) and the number of concurrent tails is capped.
  - :func:`snapshot_windows` — on session export, write a current tail of every
    configured window into ``artifacts/logs/<key>.log`` so the ZIP always carries the
    logs the operator chose to watch, whether or not a pane was ever opened.

Trust / safety (same posture as ``core/local_exec.py`` and the cmd States): the path is
operator-authored config and is passed to ``tail`` as an **argv list** (never a shell),
so there is no injection surface. Tailing is **simulated** under ``--mock`` / ``--dry-run``
(rolling placeholder lines, no real file touched — keeps ``--mock`` self-contained) and
**disabled** under ``--no-local-commands`` (the pane shows a notice). The matching artifact
snapshot is likewise echo-only / skipped in those modes.
"""

import os
import subprocess
import threading
from typing import Callable, Dict, List, Optional, Set

from flask import request
from flask_socketio import SocketIO, join_room, leave_room

from .events import EventType
from .storage import now_iso

MAX_LOG_TAILS = 16          # concurrent base-station tails (cap subprocess fan-out)
SIM_INTERVAL = 1.5          # seconds between simulated lines under --mock/--dry-run


def logwin_room(key: str) -> str:
    return f"logwin:{key}"


def _safe_name(name: str) -> str:
    """A filesystem-safe artifact basename (keys are already bare tokens; defensive)."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "log"


class _LogTail:
    def __init__(self, key: str):
        self.key = key
        self.stop = threading.Event()
        self.subscribers: Set[str] = set()
        self.thread: Optional[threading.Thread] = None
        self.proc: Optional[subprocess.Popen] = None


class LogStreamManager:
    """Streams base-station log files to the Logs view over SocketIO."""

    def __init__(self, socketio: SocketIO, logs_getter: Callable,
                 session_getter: Optional[Callable] = None,
                 events_getter: Optional[Callable] = None,
                 simulate: bool = False, allow_local: bool = True,
                 sync_manager=None):
        self.socketio = socketio
        self.logs_getter = logs_getter            # () -> LogsRegistry
        self.session_getter = session_getter      # () -> SessionStorage | None
        self.events_getter = events_getter        # () -> EventStream | None
        self.simulate = simulate                  # mock/dry-run → placeholder lines, no I/O
        self.allow_local = allow_local            # --no-local-commands → tailing disabled
        self._tails: Dict[str, _LogTail] = {}
        self._lock = threading.Lock()
        self._register()
        if sync_manager:
            sync_manager.on_disconnect(self._on_disconnect)

    # ---- socket handlers -------------------------------------------------
    def _register(self):
        @self.socketio.on("subscribe_logwin")
        def _subscribe(data):
            key = (data or {}).get("key")
            if key:
                join_room(logwin_room(key), sid=request.sid)
                self.start(key, request.sid)

        @self.socketio.on("unsubscribe_logwin")
        def _unsubscribe(data):
            key = (data or {}).get("key")
            if key:
                leave_room(logwin_room(key), sid=request.sid)
                self.stop(key, request.sid)

    def _on_disconnect(self, sid: str):
        with self._lock:
            keys = list(self._tails.keys())
        for key in keys:
            self.stop(key, sid)

    # ---- helpers ---------------------------------------------------------
    def _window(self, key: str):
        reg = self.logs_getter() if self.logs_getter else None
        return reg.get(key) if reg else None

    def _emit(self, key: str, line: str):
        self.socketio.emit("logwin_line", {"key": key, "line": line},
                           room=logwin_room(key))

    def _persist(self, key: str, line: str):
        storage = self.session_getter() if self.session_getter else None
        if storage:
            try:
                storage.append_log(f"logwin-{key}", line)
            except Exception:                  # noqa: BLE001 — never let logging kill a tail
                pass

    def _events(self):
        return self.events_getter() if self.events_getter else None

    # ---- lifecycle -------------------------------------------------------
    def start(self, key: str, sid: str):
        with self._lock:
            tail = self._tails.get(key)
            if tail:
                tail.subscribers.add(sid)
                return
            if len(self._tails) >= MAX_LOG_TAILS:
                self._emit(key, "[ccflet] too many active log windows; close one and retry")
                return
            tail = _LogTail(key)
            tail.subscribers.add(sid)
            self._tails[key] = tail
        win = self._window(key)
        if not win:
            self._emit(key, f"[ccflet] unknown log window '{key}'")
            with self._lock:
                self._tails.pop(key, None)
            return
        tail.thread = threading.Thread(target=self._run, args=(tail, win), daemon=True)
        tail.thread.start()
        ev = self._events()
        if ev:
            ev.append(EventType.STREAM_STARTED, {"logwin": key, "path": win.path})

    def _run(self, tail: _LogTail, win):
        key = tail.key
        self._emit(key, f"[ccflet] tailing {win.path}"
                        + (f" (process: {win.process})" if win.process else ""))
        if self.simulate:
            self._run_sim(tail, win)
            return
        if not self.allow_local:
            self._emit(key, "[ccflet] base-station log tailing disabled (--no-local-commands)")
            return
        cmd = ["tail", "-n", str(win.lines), "-F", win.path]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            tail.proc = proc
            for line in proc.stdout:           # blocks per line; stop() kills the proc → EOF
                if tail.stop.is_set():
                    break
                line = line.rstrip("\n")
                self._emit(key, line)
                self._persist(key, line)
        except Exception as e:                 # noqa: BLE001 — surface, don't 500 the thread
            self._emit(key, f"[ccflet] tail error: {e}")
        finally:
            self._kill(tail)

    def _run_sim(self, tail: _LogTail, win):
        """--mock/--dry-run: emit rolling placeholder lines so the Logs view is alive
        without touching the base station (mirrors local-exec echo-only)."""
        key = tail.key
        self._emit(key, "[ccflet] simulated stream (--mock/--dry-run) — file not read")
        i = 0
        while not tail.stop.is_set():
            i += 1
            line = f"{win.process or win.label}: simulated log line {i}"
            self._emit(key, line)
            self._persist(key, line)
            tail.stop.wait(SIM_INTERVAL)

    def _kill(self, tail: _LogTail):
        proc, tail.proc = tail.proc, None
        if proc:
            try:
                proc.terminate()
            except Exception:                  # noqa: BLE001
                pass

    def stop(self, key: str, sid: str):
        with self._lock:
            tail = self._tails.get(key)
            if not tail:
                return
            tail.subscribers.discard(sid)
            if tail.subscribers:
                return
            tail.stop.set()
            del self._tails[key]
        self._kill(tail)                       # unblock a wedged readline → thread exits
        ev = self._events()
        if ev:
            ev.append(EventType.STREAM_STOPPED, {"logwin": key})

    def stop_all(self):
        with self._lock:
            tails = list(self._tails.values())
            self._tails.clear()
        for tail in tails:
            tail.stop.set()
            self._kill(tail)


# ---- artifact capture (session ZIP export) ----------------------------------
def _read_tail(path: str, lines: int, max_bytes: int = 512 * 1024) -> str:
    """Return up to the last ``lines`` lines of ``path`` (bounded read). Never raises."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
    except OSError as e:
        return f"[could not read {path}: {e}]\n"
    rows = data.decode("utf-8", "replace").splitlines()
    if lines and len(rows) > lines:
        rows = rows[-lines:]
    body = "\n".join(rows)
    return (body + "\n") if body else "[empty]\n"


def snapshot_windows(windows: List, storage, simulate: bool = False,
                     allow_local: bool = True) -> List[Dict]:
    """Write a current tail of every configured window into ``artifacts/logs/<key>.log``.

    Called on session export so the ZIP always carries the logs the operator defined —
    whether or not a pane was opened live (P6). Echo-only under ``--mock``/``--dry-run``
    and skipped per-file when base-station local exec is disabled. Returns a list of
    ``{key, path|error, ok}`` describing what was written (for the audit/response).
    """
    out_dir = os.path.join(storage.artifacts_dir, "logs")
    written: List[Dict] = []
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        return [{"key": "*", "error": f"mkdir failed: {e}", "ok": False}]
    for win in windows:
        dest = os.path.join(out_dir, _safe_name(win.key) + ".log")
        header = (f"# ccFleet log artifact\n# window: {win.key}\n# label: {win.label}\n"
                  f"# process: {win.process or '-'}\n# path: {win.path}\n"
                  f"# captured_utc: {now_iso()}\n# lines: {win.lines}\n\n")
        if simulate:
            body = "[simulated run — base-station logs are not captured under --mock/--dry-run]\n"
        elif not allow_local:
            body = "[base-station local exec disabled (--no-local-commands) — not captured]\n"
        else:
            body = _read_tail(win.path, win.lines)
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(header + body)
            written.append({"key": win.key,
                            "path": os.path.relpath(dest, storage.session_dir),
                            "ok": True})
        except OSError as e:
            written.append({"key": win.key, "error": str(e), "ok": False})
    return written
