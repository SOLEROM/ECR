"""
Session storage for ccflet.

An ops session = one run of the up → watch → down loop. A session dir holds:
    manifest.json          session metadata
    events.jsonl           append-only audit
    fleet_snapshot.yaml    the fleet inventory as it was at session start
    logs/                  captured log tails
    artifacts/             pulled files
The whole dir is ZIP-exportable for the audit trail.
"""

import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, fields, asdict
from enum import Enum


class SessionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class SessionManifest:
    """Manifest for an ops session, stored as manifest.json."""
    session_id: str
    name: str
    status: str
    created_at: str
    closed_at: Optional[str] = None
    variant: str = "A"
    algo: str = "default"
    node_names: List[str] = field(default_factory=list)
    operators: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> "SessionManifest":
        d = json.loads(json_str)
        # tolerate unknown/legacy keys so an older manifest never breaks listing
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


class SessionStorage:
    """Manages storage for a single ops session."""

    def __init__(self, session_dir: str):
        self.session_dir = session_dir
        self.artifacts_dir = os.path.join(session_dir, "artifacts")
        self.logs_dir = os.path.join(session_dir, "logs")
        self.manifest_path = os.path.join(session_dir, "manifest.json")
        self.events_path = os.path.join(session_dir, "events.jsonl")
        self.fleet_snapshot_path = os.path.join(session_dir, "fleet_snapshot.yaml")

    def initialize(self, manifest: SessionManifest, fleet_yaml: str):
        os.makedirs(self.session_dir, exist_ok=True)
        os.makedirs(self.artifacts_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)
        self.save_manifest(manifest)
        with open(self.fleet_snapshot_path, "w", encoding="utf-8") as f:
            f.write(fleet_yaml)
        open(self.events_path, "a").close()

    def save_manifest(self, manifest: SessionManifest):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            f.write(manifest.to_json())

    def load_manifest(self) -> Optional[SessionManifest]:
        if not os.path.exists(self.manifest_path):
            return None
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            return SessionManifest.from_json(f.read())

    def append_log(self, logname: str, line: str):
        """Persist a streamed log line into logs/<logname>.log."""
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in logname)
        path = os.path.join(self.logs_dir, f"{safe}.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")

    def create_archive(self) -> str:
        """Create a zip archive of the entire session. Returns the archive path."""
        archive_name = os.path.basename(self.session_dir)
        archive_path = os.path.join(
            os.path.dirname(self.session_dir), f"{archive_name}.zip"
        )
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(self.session_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(
                        file_path, os.path.dirname(self.session_dir)
                    )
                    zf.write(file_path, arcname)
        return archive_path


class SessionManager:
    """Manages all ops-session storage under a runs dir."""

    def __init__(self, runs_dir: str):
        self.runs_dir = runs_dir
        os.makedirs(runs_dir, exist_ok=True)

    def generate_session_id(self, name: Optional[str] = None) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        if name:
            safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
            return f"{timestamp}_{safe_name[:50]}"
        return timestamp

    def _safe_dir(self, session_id: str) -> Optional[str]:
        """Resolve session_id under runs_dir, rejecting traversal (../, abs paths)."""
        if not session_id:
            return None
        root = os.path.realpath(self.runs_dir)
        cand = os.path.realpath(os.path.join(self.runs_dir, session_id))
        if cand != root and not cand.startswith(root + os.sep):
            return None
        return cand

    def create_session(
        self, manifest: SessionManifest, fleet_yaml: str
    ) -> SessionStorage:
        session_dir = self._safe_dir(manifest.session_id)
        if session_dir is None:
            raise ValueError(f"unsafe session id: {manifest.session_id!r}")
        storage = SessionStorage(session_dir)
        storage.initialize(manifest, fleet_yaml)
        return storage

    def get_session(self, session_id: str) -> Optional[SessionStorage]:
        session_dir = self._safe_dir(session_id)
        if session_dir and os.path.isdir(session_dir):
            return SessionStorage(session_dir)
        return None

    def list_sessions(self) -> List[Dict[str, Any]]:
        sessions = []
        for entry in os.listdir(self.runs_dir):
            session_dir = os.path.join(self.runs_dir, entry)
            if os.path.isdir(session_dir):
                manifest = SessionStorage(session_dir).load_manifest()
                if manifest:
                    sessions.append(manifest.to_dict())
        sessions.sort(key=lambda x: x["created_at"], reverse=True)
        return sessions

    def delete_session(self, session_id: str) -> bool:
        session_dir = self._safe_dir(session_id)
        if session_dir and os.path.isdir(session_dir):
            shutil.rmtree(session_dir)
            return True
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
