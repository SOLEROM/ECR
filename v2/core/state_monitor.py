"""
Status-LED monitor for ccflet — drives the **States bar** under the header.

Polls every configured state indicator (``core/states.py``) **from the base station**
and broadcasts the result over SocketIO (``states_status``) so each LED takes its color.
When a poll finds an LED's color *changed* from the previous poll it also fires an
optional ``on_change`` hook — CCFletApp wires that to a ``STATE_CHANGED`` line in the
live session log, so a link going down (or recovering) is audited like any action (P6).
Two indicator kinds, one bar:

  - **ping** — ICMP-ping an off-fleet host (reachable → green, no reply → red). The
    base-station twin of the per-node GATE poller in ``orchestrator.py``, but for
    off-fleet links (P2), never fleet nodes.
  - **cmd**  — run a command on the base station; its **exit code** selects a color via
    the indicator's ``return_colors`` map.

Under ``--mock`` / ``--dry-run`` nothing is actually pinged or run — every indicator
reports its "healthy" color (green for ping, the exit-0 color for cmd), so the mock
lights the whole bar without touching the network or the base station, mirroring the
echo-only discipline of local custom commands. cmd indicators are additionally reported
neutral (gray) when base-station local exec is disabled (``--no-local-commands``), since
they run shell here — the higher-blast-radius path.

I/O shell: the pure logic (what to check, parsing, color mapping) lives in
``core/states.py`` / ``core/networks.py``; this file only wraps ``subprocess`` / threads.
The pinger and the command runner are both injectable so the monitor is unit-tested with
no network or subprocess (``tests/test_states.py``).
"""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from .local_exec import run_local
from .states import GRAY, Indicator, StateRegistry

MAX_WORKERS = 8


def ping_once(host: str, timeout: float = 1.0) -> bool:
    """One ICMP echo to ``host``; True iff it replies. Never raises.

    Uses ``ping -c 1 -w <deadline>`` — the ``-c``/``-w`` flags are common to both
    iputils (Linux) and BusyBox ``ping``. A subprocess timeout backstops a wedged
    binary. ``host`` is a config-validated bare token and is passed as an argv element
    (no shell), so there is no injection surface here.
    """
    deadline = max(1, int(round(timeout)))
    cmd = ["ping", "-c", "1", "-w", str(deadline), host]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=deadline + 2)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _run_cmd(cmd: str, timeout: float) -> int:
    """Default cmd-state runner: run ``cmd`` on the base station, return its exit code."""
    return run_local(cmd, timeout=timeout, dry_run=False).exit_code


def _state(ind: Indicator, color: str, detail: str = "") -> Dict[str, Any]:
    """One LED's wire/REST payload — operator-authored fields rendered client-side
    with ``textContent`` (keep the XSS discipline)."""
    return {"key": ind.key, "label": ind.label, "kind": ind.kind,
            "color": color, "detail": detail, "hint": ind.hint}


class StateMonitor:
    """Polls the configured state indicators and broadcasts their colors for the bar."""

    def __init__(self, registry: StateRegistry, sync_manager=None, simulate: bool = False,
                 allow_local: bool = True,
                 pinger: Callable[[str, float], bool] = ping_once,
                 runner: Callable[[str, float], int] = _run_cmd,
                 on_change: Optional[Callable[[Dict[str, Any], str], None]] = None):
        self.registry = registry          # reloaded in place by CCFletApp.reload_config
        self.sync = sync_manager
        self.simulate = simulate          # mock/dry-run → report healthy, never touch I/O
        self.allow_local = allow_local    # --no-local-commands → cmd states stay neutral
        self._pinger = pinger
        self._runner = runner
        # fired (state, old_color) when an LED's color flips between polls — wired by
        # CCFletApp to drop a STATE_CHANGED line in the live session log (audit, P6).
        self._on_change = on_change
        self._states: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- evaluate one indicator -----------------------------------------
    def evaluate(self, ind: Indicator) -> Dict[str, Any]:
        if ind.kind == "ping":
            return self._eval_ping(ind)
        if ind.kind == "cmd":
            return self._eval_cmd(ind)
        return _state(ind, GRAY, "unknown state kind")

    def _eval_ping(self, ind: Indicator) -> Dict[str, Any]:
        if self.simulate:
            return _state(ind, "green", "connected (simulated)")
        try:
            up = bool(self._pinger(ind.host, ind.timeout))
        except Exception:                 # noqa: BLE001 — a pinger crash → down, not a 500
            up = False
        return _state(ind, "green" if up else "red",
                      ("connected" if up else "no reply") + f" ({ind.host})")

    def _eval_cmd(self, ind: Indicator) -> Dict[str, Any]:
        if self.simulate:
            return _state(ind, ind.color_for_code(0), "ok (simulated)")
        if not self.allow_local:
            return _state(ind, GRAY, "local exec disabled (--no-local-commands)")
        try:
            code = int(self._runner(ind.cmd, ind.timeout))
        except Exception as e:            # noqa: BLE001 — a runner crash → default color
            return _state(ind, ind.default_color, f"run error: {e}")
        return _state(ind, ind.color_for_code(code), f"exit {code}")

    # ---- one poll --------------------------------------------------------
    def poll_once(self) -> Dict[str, Dict[str, Any]]:
        """Evaluate every indicator (in parallel so one slow check can't stall the
        others), cache the result and broadcast it. Returns ``{key: state}``."""
        inds = list(self.registry.indicators)   # snapshot — reload mutates the list
        states: Dict[str, Dict[str, Any]] = {}
        if inds:
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(inds))) as ex:
                futs = {ex.submit(self.evaluate, ind): ind for ind in inds}
                for fut, ind in futs.items():
                    try:
                        states[ind.key] = fut.result()
                    except Exception:        # noqa: BLE001 — never let one check 500 the poll
                        states[ind.key] = _state(ind, ind.default_color or GRAY,
                                                 "check failed")
        with self._lock:
            prev = self._states
            self._states = states
        self._broadcast(states)
        self._notify_changes(prev, states)
        return states

    def _notify_changes(self, prev: Dict[str, Dict[str, Any]],
                        states: Dict[str, Dict[str, Any]]):
        """Fire ``on_change`` for every indicator whose color flipped since the last
        poll. The first poll (no prior reading for a key) is the baseline and emits
        nothing — so boot, a fresh start, or a config reload doesn't spam the session
        log; only genuine transitions are recorded."""
        if not self._on_change:
            return
        for ind in self.registry.indicators:
            new = states.get(ind.key)
            old = prev.get(ind.key)
            if not new or not old:
                continue                       # new/removed indicator → no transition
            if old.get("color") != new.get("color"):
                try:
                    self._on_change(new, old.get("color"))
                except Exception:              # noqa: BLE001 — a logging hook must never kill the poll
                    pass

    # ---- read ------------------------------------------------------------
    def snapshot(self) -> List[Dict[str, Any]]:
        """LED states in registry order; indicators not yet polled report ``color:
        gray`` (a neutral light until the first check lands)."""
        with self._lock:
            states = dict(self._states)
        return [states.get(ind.key, _state(ind, GRAY, "checking…"))
                for ind in self.registry.indicators]

    def _broadcast(self, states: Dict[str, Dict[str, Any]]):
        if self.sync:
            ordered = [states[ind.key] for ind in self.registry.indicators
                       if ind.key in states]
            self.sync.broadcast_states_status(ordered)

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception:            # noqa: BLE001 — never let the LED thread die
                    pass
                self._stop.wait(self.registry.poll_interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
