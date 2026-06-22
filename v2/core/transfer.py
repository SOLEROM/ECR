"""
File transfer for ccflet (hybrid SSH/transfer).

We shell out to the system binaries (not paramiko SFTP) for bulk code push:
  - roleA : rsync -avzP -e "ssh <ssh_opts>" <src> <user>@<host>:<dst>
  - roleB : scp -O <ssh_opts> -J <roleA_user>@<roleA_host> <local> <user>@<host>:<dst>
            (reached through roleA as a jump-host; use scp where the target has no
            sftp-server, so SFTP/put_file won't work there)

The command-synthesis functions are pure and unit-tested; `run_transfer` executes
them via subprocess and returns the same CommandResult shape as an SSH action, so
transfers are audited identically. `--dry-run` returns the command unrun.
"""

import shlex
import subprocess
import time
from typing import List

from .result import CommandResult


def rsync_push_cmd(src: str, dst: str, user: str, host: str, ssh_opts: str = "") -> List[str]:
    """rsync the local `src` dir/file to `user@host:dst` over ssh."""
    ssh_cmd = "ssh " + ssh_opts if ssh_opts else "ssh"
    return ["rsync", "-avzP", "-e", ssh_cmd, src, f"{user}@{host}:{dst}"]


def scp_to_roleB_cmd(local: str, remote: str, roleA_user: str, roleA_host: str,
                     roleB_user: str, roleB_host: str, ssh_opts: str = "") -> List[str]:
    """scp -O the local file to roleB, jumping through roleA."""
    cmd = ["scp", "-O"]
    if ssh_opts:
        cmd += shlex.split(ssh_opts)
    cmd += ["-J", f"{roleA_user}@{roleA_host}", local, f"{roleB_user}@{roleB_host}:{remote}"]
    return cmd


def _result(cmd: List[str], proc, start: float) -> CommandResult:
    return CommandResult(
        command=" ".join(shlex.quote(c) for c in cmd),
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        start_time=start,
        end_time=time.time(),
    )


def run_transfer(cmd: List[str], timeout: int = 300, dry_run: bool = False) -> CommandResult:
    """Execute a transfer command list. dry_run echoes it without running."""
    pretty = " ".join(shlex.quote(c) for c in cmd)
    start = time.time()
    if dry_run:
        return CommandResult(pretty, 0, f"[dry-run] {pretty}", "", start, time.time())
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return _result(cmd, proc, start)
    except subprocess.TimeoutExpired:
        return CommandResult(pretty, -1, "", f"transfer timed out after {timeout}s",
                             start, time.time())
    except FileNotFoundError as e:
        return CommandResult(pretty, -1, "", f"binary not found: {e}", start, time.time())
    except Exception as e:  # noqa: BLE001
        return CommandResult(pretty, -1, "", str(e), start, time.time())
