"""
Base-station connectivity monitor for ccflet — drives the top-bar LEDs.

A small background poller that ICMP-pings each configured link (``core/networks.py``)
**from the base station** and broadcasts the up/down result over SocketIO
(``net_status``) so the header LEDs go green (reachable) / red (no reply). It is the
base-station twin of the per-node GATE poller in ``orchestrator.py``, but for
**off-fleet** links (the configured gateway/upstream/peer targets), not fleet nodes.

Under ``--mock`` / ``--dry-run`` nothing is actually pinged — every link reports
**up** (the mock lights the whole UI green without touching the network), mirroring
the echo-only discipline of local commands (``orchestrator._exec_local``).

I/O shell: the pure logic (what to ping, parsing the config) lives in
``core/networks.py``; this file only wraps ``subprocess``/threads. The pinger is
injectable so the monitor is unit-tested with no network (``tests/test_networks.py``).
"""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from .networks import NetLink, Networks

MAX_PING_WORKERS = 8


def ping_once(host: str, timeout: float = 1.0) -> bool:
    """One ICMP echo to ``host``; True iff it replies. Never raises.

    Uses ``ping -c 1 -w <deadline>`` — the ``-c``/``-w`` flags are common to both
    iputils (Linux) and BusyBox ``ping``. A subprocess timeout backstops a wedged
    binary. ``host`` is a config-validated bare token and is passed as an argv
    element (no shell), so there is no injection surface here.
    """
    deadline = max(1, int(round(timeout)))
    cmd = ["ping", "-c", "1", "-w", str(deadline), host]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=deadline + 2)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _state(link: NetLink, up: Optional[bool]) -> Dict[str, Any]:
    """One LED's wire/REST payload. ``up`` is True/False, or None = not yet checked."""
    return {"key": link.key, "label": link.label, "host": link.host,
            "hint": link.hint, "up": up}


class NetMonitor:
    """Polls the configured links and broadcasts their reachability for the LEDs."""

    def __init__(self, networks: Networks, sync_manager=None, simulate: bool = False,
                 pinger: Callable[[str, float], bool] = ping_once):
        self.networks = networks          # reloaded in place by CCFletApp.reload_config
        self.sync = sync_manager
        self.simulate = simulate          # mock/dry-run → report up, never ping
        self._pinger = pinger
        self._states: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- one check -------------------------------------------------------
    def check_link(self, link: NetLink) -> Dict[str, Any]:
        up = True if self.simulate else bool(self._pinger(link.host, self.networks.ping_timeout))
        return _state(link, up)

    def poll_once(self) -> Dict[str, Dict[str, Any]]:
        """Check every link (in parallel so one dead link can't stall the others),
        cache the result and broadcast it. Returns ``{key: state}``."""
        links = list(self.networks.links)   # snapshot — reload mutates the list
        states: Dict[str, Dict[str, Any]] = {}
        if links:
            with ThreadPoolExecutor(max_workers=min(MAX_PING_WORKERS, len(links))) as ex:
                futs = {ex.submit(self.check_link, l): l for l in links}
                for fut, l in futs.items():
                    try:
                        states[l.key] = fut.result()
                    except Exception:       # noqa: BLE001 — a pinger crash → down, not a 500
                        states[l.key] = _state(l, False)
        with self._lock:
            self._states = states
        self._broadcast(states)
        return states

    # ---- read ------------------------------------------------------------
    def snapshot(self) -> List[Dict[str, Any]]:
        """LED states in config order; links not yet polled report ``up: None``
        (rendered as a neutral/gray LED until the first check lands)."""
        with self._lock:
            states = dict(self._states)
        return [states.get(l.key, _state(l, None)) for l in self.networks.links]

    def _broadcast(self, states: Dict[str, Dict[str, Any]]):
        if self.sync:
            ordered = [states[l.key] for l in self.networks.links if l.key in states]
            self.sync.broadcast_net_status(ordered)

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception:           # noqa: BLE001 — never let the LED thread die
                    pass
                self._stop.wait(self.networks.poll_interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
