"""
Storage management for ECR.
Handles run directory structure, manifests, and archiving.
"""

import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class RunManifest:
    """Manifest for a run, stored as manifest.json."""
    run_id: str
    name: str
    profile_name: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    parameters: Dict[str, str] = field(default_factory=dict)
    artifacts: List[Dict[str, str]] = field(default_factory=list)
    notes: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RunManifest':
        return cls(**data)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'RunManifest':
        return cls.from_dict(json.loads(json_str))


class RunStorage:
    """
    Manages storage for a single run.
    """
    
    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        self.artifacts_dir = os.path.join(run_dir, 'artifacts')
        self.logs_dir = os.path.join(run_dir, 'logs')
        self.manifest_path = os.path.join(run_dir, 'manifest.json')
        self.events_path = os.path.join(run_dir, 'events.jsonl')
        self.profile_snapshot_path = os.path.join(run_dir, 'profile_snapshot.yaml')
    
    def initialize(self, manifest: RunManifest, profile_yaml: str):
        """Initialize run directory structure."""
        os.makedirs(self.run_dir, exist_ok=True)
        os.makedirs(self.artifacts_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)
        
        # Write manifest
        self.save_manifest(manifest)
        
        # Write profile snapshot
        with open(self.profile_snapshot_path, 'w', encoding='utf-8') as f:
            f.write(profile_yaml)
        
        # Create empty events file
        open(self.events_path, 'a').close()
    
    def save_manifest(self, manifest: RunManifest):
        """Save manifest to disk."""
        with open(self.manifest_path, 'w', encoding='utf-8') as f:
            f.write(manifest.to_json())
    
    def load_manifest(self) -> Optional[RunManifest]:
        """Load manifest from disk."""
        if not os.path.exists(self.manifest_path):
            return None
        with open(self.manifest_path, 'r', encoding='utf-8') as f:
            return RunManifest.from_json(f.read())
    
    def add_artifact(self, local_path: str, original_remote_path: str) -> str:
        """
        Add an artifact to the run.
        Returns the path within the artifacts directory.
        """
        filename = os.path.basename(local_path)
        # Handle duplicate filenames
        dest_path = os.path.join(self.artifacts_dir, filename)
        counter = 1
        while os.path.exists(dest_path):
            name, ext = os.path.splitext(filename)
            dest_path = os.path.join(self.artifacts_dir, f"{name}_{counter}{ext}")
            counter += 1
        
        shutil.copy2(local_path, dest_path)
        return os.path.relpath(dest_path, self.run_dir)
    
    def get_artifact_path(self, relative_path: str) -> str:
        """Get full path to an artifact."""
        return os.path.join(self.run_dir, relative_path)
    
    def create_archive(self) -> str:
        """
        Create a zip archive of the entire run.
        Returns the path to the archive.
        """
        archive_name = os.path.basename(self.run_dir)
        archive_path = os.path.join(os.path.dirname(self.run_dir), f"{archive_name}.zip")
        
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(self.run_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, os.path.dirname(self.run_dir))
                    zf.write(file_path, arcname)
        
        return archive_path


class StorageManager:
    """
    Manages all run storage.
    """
    
    def __init__(self, runs_dir: str):
        self.runs_dir = runs_dir
        os.makedirs(runs_dir, exist_ok=True)
    
    def generate_run_id(self, name: Optional[str] = None) -> str:
        """Generate a unique run ID with timestamp and optional name."""
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        if name:
            # Sanitize name
            safe_name = "".join(c if c.isalnum() or c in '-_' else '-' for c in name)
            safe_name = safe_name[:50]  # Limit length
            return f"{timestamp}_{safe_name}"
        return timestamp
    
    def create_run(self, run_id: str, manifest: RunManifest, profile_yaml: str) -> RunStorage:
        """Create a new run directory."""
        run_dir = os.path.join(self.runs_dir, run_id)
        storage = RunStorage(run_dir)
        storage.initialize(manifest, profile_yaml)
        return storage
    
    def get_run(self, run_id: str) -> Optional[RunStorage]:
        """Get storage for an existing run."""
        run_dir = os.path.join(self.runs_dir, run_id)
        if os.path.exists(run_dir):
            return RunStorage(run_dir)
        return None
    
    def list_runs(self) -> List[Dict[str, Any]]:
        """List all runs with basic metadata."""
        runs = []
        for entry in os.listdir(self.runs_dir):
            run_dir = os.path.join(self.runs_dir, entry)
            if os.path.isdir(run_dir):
                storage = RunStorage(run_dir)
                manifest = storage.load_manifest()
                if manifest:
                    runs.append({
                        'run_id': manifest.run_id,
                        'name': manifest.name,
                        'profile_name': manifest.profile_name,
                        'status': manifest.status,
                        'created_at': manifest.created_at,
                        'started_at': manifest.started_at,
                        'completed_at': manifest.completed_at
                    })
        
        # Sort by creation time, newest first
        runs.sort(key=lambda x: x['created_at'], reverse=True)
        return runs
    
    def delete_run(self, run_id: str) -> bool:
        """Delete a run directory."""
        run_dir = os.path.join(self.runs_dir, run_id)
        if os.path.exists(run_dir):
            shutil.rmtree(run_dir)
            return True
        return False
