"""
Local (base-station) command execution for ccflet (config over code).

Operator commands with ``on: local`` run **here**, on the machine hosting ccflet, as a
subprocess (housekeeping: archive old runs, disk checks, …) — as opposed to
``on: remote`` commands that SSH to a fleet node. Output is captured into the same
``CommandResult`` shape as an SSH action or a transfer, so local commands are audited
identically and the UI shows them beside remote ones (with a 🖥 chip).

Safety: this is the higher-blast-radius path — it runs on the base station as the app
user. It is **echo-only** under ``--mock``/``--dry-run`` (callers pass ``dry_run``) and
the whole feature is gated by ``--no-local-commands``. No shell-injection guard is
added here because the command body is operator-authored config — that is the
deliberate config-over-code trust model (closed LAN, audited), not an oversight.
"""

import os
import shlex
import subprocess
import time

from .result import CommandResult


def run_local(command, cwd=None, env=None, timeout=60, dry_run=False, shell="bash"):
    """Run ``command`` on the base station and return a CommandResult.

    ``command`` is either a shell program string (run via ``bash -c``) or an argv list
    (run directly). ``env`` is merged onto the current environment. ``dry_run`` echoes
    the command without running it.
    """
    start = time.time()
    is_str = isinstance(command, str)
    pretty = command if is_str else " ".join(shlex.quote(c) for c in command)
    if dry_run:
        return CommandResult(pretty, 0, f"[dry-run] (local) {pretty}", "",
                             start, time.time())
    run_env = dict(os.environ)
    if env:
        run_env.update({k: str(v) for k, v in env.items()})
    argv = [shell, "-c", command] if is_str else list(command)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              cwd=cwd, env=run_env)
        return CommandResult(pretty, proc.returncode, proc.stdout, proc.stderr,
                             start, time.time())
    except subprocess.TimeoutExpired:
        return CommandResult(pretty, -1, "",
                             f"local command timed out after {timeout}s",
                             start, time.time())
    except FileNotFoundError as e:
        return CommandResult(pretty, -1, "", f"not found: {e}", start, time.time())
    except Exception as e:  # noqa: BLE001
        return CommandResult(pretty, -1, "", str(e), start, time.time())
