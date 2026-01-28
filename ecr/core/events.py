"""
Event stream management for ECR.
Handles append-only JSONL event logging with immutability guarantees.
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class EventType(str, Enum):
    # Run lifecycle
    RUN_STARTED = "run_started"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    RUN_COMPLETED = "run_completed"
    RUN_INTERRUPTED = "run_interrupted"
    
    # Stage lifecycle
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    
    # Action execution
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    
    # Command execution
    COMMAND_STARTED = "command_started"
    COMMAND_OUTPUT = "command_output"
    COMMAND_COMPLETED = "command_completed"
    COMMAND_FAILED = "command_failed"
    
    # Artifacts
    ARTIFACT_PULL_STARTED = "artifact_pull_started"
    ARTIFACT_PULLED = "artifact_pulled"
    ARTIFACT_PULL_FAILED = "artifact_pull_failed"
    
    # Background collectors
    COLLECTOR_STARTED = "collector_started"
    COLLECTOR_STOPPED = "collector_stopped"
    COLLECTOR_OUTPUT = "collector_output"
    COLLECTOR_ERROR = "collector_error"
    
    # Connection
    CONNECTION_ESTABLISHED = "connection_established"
    CONNECTION_LOST = "connection_lost"
    CONNECTION_RETRY = "connection_retry"
    
    # Operator interactions
    NOTE = "note"
    EDIT = "edit"
    PARAMETER_SET = "parameter_set"
    
    # Errors
    ERROR = "error"


@dataclass
class Event:
    """Represents a single immutable event in the stream."""
    seq: int
    timestamp: str
    event_type: str
    data: Dict[str, Any]
    
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
    
    @classmethod
    def from_json(cls, line: str) -> 'Event':
        d = json.loads(line)
        return cls(
            seq=d['seq'],
            timestamp=d['timestamp'],
            event_type=d['event_type'],
            data=d['data']
        )


class EventStream:
    """
    Append-only event stream backed by a JSONL file.
    Thread-safe for concurrent writes.
    """
    
    def __init__(self, filepath: str):
        self.filepath = filepath
        self._lock = threading.Lock()
        self._seq = 0
        
        # Initialize sequence from existing file
        if os.path.exists(filepath):
            self._seq = self._count_events()
    
    def _count_events(self) -> int:
        """Count existing events to determine next sequence number."""
        count = 0
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        count += 1
        except (IOError, json.JSONDecodeError):
            pass
        return count
    
    def append(self, event_type: EventType, data: Optional[Dict[str, Any]] = None) -> Event:
        """
        Append a new event to the stream.
        Returns the created event.
        """
        with self._lock:
            self._seq += 1
            event = Event(
                seq=self._seq,
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type=event_type.value,
                data=data or {}
            )
            
            # Append to file
            with open(self.filepath, 'a', encoding='utf-8') as f:
                f.write(event.to_json() + '\n')
                f.flush()
                os.fsync(f.fileno())
            
            return event
    
    def iter_events(self, after_seq: int = 0) -> Iterator[Event]:
        """Iterate over events, optionally starting after a given sequence number."""
        if not os.path.exists(self.filepath):
            return
        
        with open(self.filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    event = Event.from_json(line)
                    if event.seq > after_seq:
                        yield event
    
    def get_all_events(self) -> list[Event]:
        """Get all events as a list."""
        return list(self.iter_events())
    
    def get_last_event(self, event_type: Optional[EventType] = None) -> Optional[Event]:
        """Get the most recent event, optionally filtered by type."""
        events = self.get_all_events()
        if event_type:
            events = [e for e in events if e.event_type == event_type.value]
        return events[-1] if events else None
    
    @property
    def current_seq(self) -> int:
        """Return the current sequence number."""
        return self._seq
