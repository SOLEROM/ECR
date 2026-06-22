"""
Shared command-result type for ccflet.

Kept paramiko-free so supervisor / transfer / mock / status can import it without
pulling in the SSH stack — important for the no-network pure-logic test suite.
"""

import time
from dataclasses import dataclass


@dataclass
class CommandResult:
    """Result of a command execution (remote SSH, local subprocess, or mock)."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration": round(self.duration, 3),
            "success": self.success,
        }


def now() -> float:
    return time.time()
