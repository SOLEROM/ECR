"""
Daemon supervision over SSH for ccflet.

Detached + pidfile supervision, preferring systemd for a daemon where its unit is
installed (e.g. serviceA):
  start  : `mkdir -p /tmp/ccflet; setsid nohup sh -c '<CMD>' >log 2>&1 </dev/null &
            echo $! >pidfile`  (or `systemctl start <unit>` when preferred)
  status : pidfile pid alive (`kill -0`) → else `pgrep -f <match>` → else
            `systemctl is-active`  →  {up, pid, source}
  stop   : pidfile TERM→KILL, fallback `pkill -f <match>` (or `systemctl stop`)

The command-synthesis functions are pure and unit-tested with no network; the
Supervisor class just runs them through an ssh client and parses the result.
"""

import shlex
from typing import Optional, Dict, Any

from .result import CommandResult

PID_DIR = "/tmp/ccflet"


def _sq(s: str) -> str:
    """Single-quote for safe embedding in an outer sh -c '...'."""
    return "'" + s.replace("'", "'\\''") + "'"


def detached_start_cmd(daemon: str, command: str, pid_dir: str = PID_DIR) -> str:
    """Synthesize a detached start that survives the SSH session and records a pid."""
    logf = f"{pid_dir}/{daemon}.log"
    pidf = f"{pid_dir}/{daemon}.pid"
    inner = _sq(command)
    return (
        f"mkdir -p {pid_dir}; "
        f"setsid nohup sh -c {inner} >{logf} 2>&1 </dev/null & "
        f"echo $! >{pidf}; "
        f"echo ccflet-started pid=$(cat {pidf}) daemon={daemon}"
    )


def systemd_start_cmd(unit: str) -> str:
    return f"systemctl start {shlex.quote(unit)} && echo ccflet-started source=systemd unit={unit}"


def unit_installed_cmd(unit: str) -> str:
    """Probe whether a systemd unit exists (rc 0 → installed)."""
    return f"systemctl cat {shlex.quote(unit)} >/dev/null 2>&1"


def status_cmd(daemon: str, match: Optional[str], unit: Optional[str] = None,
               pid_dir: str = PID_DIR) -> str:
    pidf = f"{pid_dir}/{daemon}.pid"
    match = match or daemon
    parts = [
        f'if [ -f {pidf} ] && kill -0 "$(cat {pidf} 2>/dev/null)" 2>/dev/null; then '
        f'echo "up pid=$(cat {pidf}) source=pidfile";',
        f'elif pgrep -f {_sq(match)} >/dev/null 2>&1; then '
        f'echo "up pid=$(pgrep -f {_sq(match)} | head -1) source=pgrep";',
    ]
    if unit:
        parts.append(
            f'elif systemctl is-active --quiet {shlex.quote(unit)} 2>/dev/null; then '
            f'echo "up source=systemd";'
        )
    parts.append('else echo down; fi')
    return " ".join(parts)


def stop_cmd(daemon: str, match: Optional[str], unit: Optional[str] = None,
             pid_dir: str = PID_DIR) -> str:
    pidf = f"{pid_dir}/{daemon}.pid"
    match = match or daemon
    lines = []
    if unit:
        lines.append(f"systemctl stop {shlex.quote(unit)} 2>/dev/null;")
    lines.append(
        f'if [ -f {pidf} ]; then P=$(cat {pidf}); '
        f'kill "$P" 2>/dev/null; sleep 1; kill -9 "$P" 2>/dev/null; rm -f {pidf}; fi;'
    )
    lines.append(f"pkill -f {_sq(match)} 2>/dev/null;")
    lines.append("echo ccflet-stopped")
    return " ".join(lines)


def parse_status_output(stdout: str) -> Dict[str, Any]:
    """Parse the status_cmd output → {up: bool, pid: int|None, source: str}."""
    text = (stdout or "").strip()
    if not text or text.splitlines()[-1].strip() == "down":
        return {"up": False, "pid": None, "source": None}
    line = text.splitlines()[-1].strip()
    if not line.startswith("up"):
        return {"up": False, "pid": None, "source": None}
    pid = None
    source = None
    for tok in line.split():
        if tok.startswith("pid="):
            try:
                pid = int(tok[4:])
            except ValueError:
                pid = None
        elif tok.startswith("source="):
            source = tok[7:]
    return {"up": True, "pid": pid, "source": source}


class Supervisor:
    """Runs the synthesized supervision commands through an ssh client."""

    def __init__(self, ssh, pid_dir: str = PID_DIR):
        self.ssh = ssh
        self.pid_dir = pid_dir

    def start(self, daemon: str, command: str, prefer_systemd: Optional[str] = None,
              timeout: int = 30) -> CommandResult:
        if prefer_systemd:
            probe = self.ssh.execute(unit_installed_cmd(prefer_systemd), timeout=timeout)
            if probe.success:
                return self.ssh.execute(systemd_start_cmd(prefer_systemd), timeout=timeout)
        return self.ssh.execute(
            detached_start_cmd(daemon, command, self.pid_dir), timeout=timeout
        )

    def status(self, daemon: str, match: Optional[str], unit: Optional[str] = None,
               timeout: int = 10) -> Dict[str, Any]:
        res = self.ssh.execute(
            status_cmd(daemon, match, unit, self.pid_dir), timeout=timeout
        )
        parsed = parse_status_output(res.stdout)
        parsed["raw"] = res.stdout.strip()
        return parsed

    def stop(self, daemon: str, match: Optional[str], unit: Optional[str] = None,
             timeout: int = 15) -> CommandResult:
        return self.ssh.execute(
            stop_cmd(daemon, match, unit, self.pid_dir), timeout=timeout
        )
