"""
Append-only JSONL audit stream for ccflet (thread-safe, fsync'd).

The event-type set is fleet-first: actions, variant-aware sequences, daemon
lifecycle, deploy, collectors, GATE transitions and live-stream lifecycle.
*Every* action invocation and its result is appended here — the audit log is the
safety net (no confirmation prompts, no dry-run gating in the UI).
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class EventType(str, Enum):
    # Session lifecycle
    SESSION_STARTED = "session_started"
    SESSION_CLOSED = "session_closed"
    SESSION_RENAMED = "session_renamed"

    # Single action (one kind against one node)
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"

    # Variant-aware ordered sequences (bring_up / tear_down / deploy)
    SEQUENCE_STARTED = "sequence_started"
    SEQUENCE_STEP = "sequence_step"
    SEQUENCE_COMPLETED = "sequence_completed"
    SEQUENCE_FAILED = "sequence_failed"

    # Daemon lifecycle (serviceA / serviceB / serviceC)
    DAEMON_STARTED = "daemon_started"
    DAEMON_STOPPED = "daemon_stopped"
    DAEMON_STATUS = "daemon_status"

    # Deploy (rsync / scp / build)
    DEPLOY_STARTED = "deploy_started"
    DEPLOY_COMPLETED = "deploy_completed"
    DEPLOY_FAILED = "deploy_failed"

    # Status collectors + health
    COLLECTOR_OUTPUT = "collector_output"
    COLLECTOR_ERROR = "collector_error"
    GATE_CHANGED = "gate_changed"

    # Live log streaming
    STREAM_STARTED = "stream_started"
    STREAM_STOPPED = "stream_stopped"

    # Connection
    CONNECTION_ESTABLISHED = "connection_established"
    CONNECTION_LOST = "connection_lost"
    CONNECTION_RETRY = "connection_retry"

    # Config-over-code: operator edits a logic file from the Config page
    CONFIG_SAVED = "config_saved"
    CONFIG_RELOADED = "config_reloaded"

    # Operator interactions
    NOTE = "note"
    ERROR = "error"


@dataclass
class Event:
    """A single immutable event in the stream."""
    seq: int
    timestamp: str
    event_type: str
    data: Dict[str, Any]
    user: Optional[Dict[str, str]] = None  # {username, color} of who triggered

    def to_dict(self) -> Dict[str, Any]:
        """UI/wire shape — uses `type` (what the dashboard + API consume)."""
        d = {"seq": self.seq, "timestamp": self.timestamp,
             "type": self.event_type, "data": self.data}
        if self.user is not None:
            d["user"] = self.user
        return d

    def _disk_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["user"] is None:
            del d["user"]
        return d

    def to_json(self) -> str:
        """On-disk JSONL shape — keeps `event_type` (round-trips via from_json)."""
        return json.dumps(self._disk_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "Event":
        d = json.loads(line)
        return cls(
            seq=d["seq"],
            timestamp=d["timestamp"],
            event_type=d["event_type"],
            data=d["data"],
            user=d.get("user"),
        )


class EventStream:
    """Append-only event stream backed by a JSONL file. Thread-safe."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._lock = threading.Lock()
        self._seq = 0
        if os.path.exists(filepath):
            self._seq = self._count_events()

    def _count_events(self) -> int:
        count = 0
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        count += 1
        except (IOError, json.JSONDecodeError):
            pass
        return count

    def append(
        self,
        event_type: EventType,
        data: Optional[Dict[str, Any]] = None,
        user: Optional[Dict[str, str]] = None,
    ) -> Event:
        """Append a new event and return it (durably fsync'd)."""
        with self._lock:
            self._seq += 1
            event = Event(
                seq=self._seq,
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type=event_type.value if isinstance(event_type, EventType) else str(event_type),
                data=data or {},
                user=user,
            )
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(event.to_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            return event

    def iter_events(self, after_seq: int = 0) -> Iterator[Event]:
        if not os.path.exists(self.filepath):
            return
        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    event = Event.from_json(line)
                    if event.seq > after_seq:
                        yield event

    def get_all_events(self) -> list:
        return list(self.iter_events())

    @property
    def current_seq(self) -> int:
        return self._seq
