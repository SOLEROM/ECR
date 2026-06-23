"""
The acceptance gate (R6) — the correctness oracle.

A build is "done" only when, **inside the built app dir**:
  1. ``pytest`` passes (pure-logic + mock-backed suites, against the app's domain),
  2. ``app.py --mock`` boots and the simulated fleet *lights up* (the mock↔status
     string contract holds — at least one node reaches GATE A = ok after a bring-up),
  3. ``app.py --dry-run`` synthesises commands (a deploy returns ``[dry-run] …``).

Each check returns ``(ok, log)``; :func:`run_gate` ANDs them. The pipeline treats a
red gate as "ship nothing" (exit non-zero). Network-free and self-contained: it drives
the app over HTTP on an ephemeral port, then tears the server down.
"""

import json
import os
import socket
import subprocess
import time
import urllib.request
from typing import Optional, Tuple


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http(url: str, method: str = "GET", data: bytes = None,
          timeout: float = 5.0) -> Tuple[int, str]:
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def _wait_http(url: str, tries: int = 40, delay: float = 0.25) -> bool:
    for _ in range(tries):
        code, _ = _http(url, timeout=2.0)
        if code == 200:
            return True
        time.sleep(delay)
    return False


def run_pytest(app_dir: str, python: str) -> Tuple[bool, str]:
    try:
        r = subprocess.run([python, "-m", "pytest", "-q"], cwd=app_dir,
                           capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "pytest: TIMEOUT"
    tail = "\n".join((r.stdout + r.stderr).splitlines()[-8:])
    return r.returncode == 0, tail


def _boot(app_dir: str, python: str, extra: list, port: int,
          poll: bool = False) -> subprocess.Popen:
    args = [python, "app.py", "--port", str(port), *extra]
    if not poll:
        args.append("--no-poll")
    return subprocess.Popen(
        args, cwd=app_dir, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        start_new_session=True)


def _first_node(base: str) -> Optional[str]:
    code, body = _http(base + "/api/fleet")
    if code != 200:
        return None
    try:
        nodes = json.loads(body).get("nodes", [])
        return (nodes[0]["name"] if nodes and isinstance(nodes[0], dict)
                else (nodes[0] if nodes else None))
    except (ValueError, KeyError, IndexError):
        return None


def _kill(proc: Optional[subprocess.Popen]):
    if not proc:
        return
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except (ProcessLookupError, PermissionError):
            pass


def _gate_ok(base: str, node: str) -> bool:
    """A node's GATE A + B are both ``ok`` (the fleet lit up). ``/api/node/<n>/status``
    forces a fresh synchronous poll, so it reflects the mock world immediately."""
    code, body = _http(base + f"/api/node/{node}/status")
    if code != 200:
        return False
    try:
        gates = json.loads(body).get("gates", {})
    except ValueError:
        return False
    return (gates.get("A", {}).get("state") == "ok"
            and gates.get("B", {}).get("state") == "ok")


def run_mock_boot(app_dir: str, python: str) -> Tuple[bool, str]:
    """Boot --mock, bring the fleet up, and confirm a node reaches GATE A+B = ok."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = _boot(app_dir, python, ["--mock"], port, poll=True)
    try:
        if not _wait_http(base + "/"):
            return False, "--mock: server did not answer on /"
        node = _first_node(base) or "node1"
        _http(base + "/api/fleet/bring_up", "POST", b"{}")
        for _ in range(30):                       # bring-up is async + staggered
            if _gate_ok(base, node):
                return True, f"--mock: fleet lit up ({node} GATE A+B ok)"
            time.sleep(0.5)
        _, body = _http(base + f"/api/node/{node}/status")
        return False, f"--mock: {node} did not light up; status head: {body[:160]}"
    finally:
        _kill(proc)


def run_dry_run(app_dir: str, python: str) -> Tuple[bool, str]:
    """Boot --dry-run and confirm an action synthesises a printed command."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = _boot(app_dir, python, ["--dry-run"], port)
    try:
        if not _wait_http(base + "/"):
            return False, "--dry-run: server did not answer on /"
        node = _first_node(base) or "node1"
        code, body = _http(base + f"/api/node/{node}/action", "POST",
                           b'{"role":"roleA","action":"serviceA_start"}')
        if code == 200 and "[dry-run]" in body:
            return True, "--dry-run: commands synthesised ([dry-run] …)"
        return False, f"--dry-run: no synthesised command; status={code} head={body[:160]}"
    finally:
        _kill(proc)


def run_gate(app_dir: str, python: str) -> Tuple[bool, str]:
    """Run the full acceptance gate; return (passed, multi-line log)."""
    lines = []
    ok_all = True
    for name, fn in (("pytest", run_pytest), ("--mock boot", run_mock_boot),
                     ("--dry-run", run_dry_run)):
        ok, log = fn(app_dir, python)
        ok_all = ok_all and ok
        lines.append(f"  [{'PASS' if ok else 'FAIL'}] {name}: {log}")
        if not ok and name == "pytest":
            break  # no point booting an app whose units are red
    return ok_all, "\n".join(lines)
