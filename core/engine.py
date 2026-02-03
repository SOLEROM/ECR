"""
Experiment execution engine for ECR.
Orchestrates runs, commands, and background collectors.
"""

import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

from .events import EventStream, EventType
from .ssh_client import SSHClientWrapper, ConnectionConfig, CommandResult
from .profiles import (
    TargetProfile, CommandDefinition, CollectorDefinition,
    substitute_parameters, get_command_parameters
)
from .storage import RunStorage, RunManifest, RunStatus


@dataclass
class BackgroundCollector:
    """Running background collector."""
    name: str
    definition: CollectorDefinition
    thread: Optional[threading.Thread] = None
    stop_event: Optional[threading.Event] = None
    running: bool = False


@dataclass 
class RunContext:
    """Context for an active run."""
    run_id: str
    storage: RunStorage
    manifest: RunManifest
    profile: TargetProfile
    events: EventStream
    ssh: Optional[SSHClientWrapper] = None
    parameters: Dict[str, str] = field(default_factory=dict)
    collectors: Dict[str, BackgroundCollector] = field(default_factory=dict)
    is_running: bool = False
    is_paused: bool = False


def execute_host_command(command: str, timeout: int = 60) -> CommandResult:
    """Execute a command on the host (controller) machine."""
    start_time = time.time()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return CommandResult(
            command=command,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            start_time=start_time,
            end_time=time.time()
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            command=command,
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            start_time=start_time,
            end_time=time.time()
        )
    except Exception as e:
        return CommandResult(
            command=command,
            exit_code=-1,
            stdout="",
            stderr=str(e),
            start_time=start_time,
            end_time=time.time()
        )


class ExperimentEngine:
    """
    Main experiment execution engine.
    Manages runs, SSH connections, and background collectors.
    """
    
    def __init__(self, storage_manager, profile_manager):
        self.storage_manager = storage_manager
        self.profile_manager = profile_manager
        self._active_runs: Dict[str, RunContext] = {}
        self._lock = threading.RLock()
        
        # Event callbacks for UI updates (receives run_id, event_dict)
        self._event_callbacks: List[Callable[[str, Dict], None]] = []
    
    def add_event_callback(self, callback: Callable[[str, Dict], None]):
        """Add callback for real-time event notifications."""
        self._event_callbacks.append(callback)
    
    def _notify_event(self, run_id: str, event):
        """Notify all callbacks of a new event."""
        event_dict = {
            'seq': event.seq,
            'timestamp': event.timestamp,
            'type': event.event_type,
            'data': event.data
        }
        if event.user:
            event_dict['user'] = event.user
            
        for callback in self._event_callbacks:
            try:
                callback(run_id, event_dict)
            except Exception as e:
                print(f"Event callback error: {e}")
    
    def _notify(self, event_type: str, data: Any):
        """Legacy notify - kept for compatibility."""
        pass
    
    def create_run(
        self,
        profile_name: str,
        name: Optional[str] = None,
        parameters: Optional[Dict[str, str]] = None,
        selected_commands: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Create a new run.
        Returns the run_id or None on failure.
        """
        # Load profile
        profile = self.profile_manager.load_profile(profile_name)
        if not profile:
            return None
        
        # Default to all commands if none selected
        if selected_commands is None:
            selected_commands = list(profile.commands.keys())
        
        # Generate run ID
        run_id = self.storage_manager.generate_run_id(name)
        now = datetime.now(timezone.utc).isoformat()
        
        # Create manifest
        manifest = RunManifest(
            run_id=run_id,
            name=name or run_id,
            profile_name=profile_name,
            status=RunStatus.CREATED.value,
            created_at=now,
            parameters=parameters or {},
            selected_commands=selected_commands
        )
        
        # Create storage
        storage = self.storage_manager.create_run(
            run_id, manifest, profile.to_yaml()
        )
        
        # Create event stream (no event logged yet - starts with RUN_STARTED)
        EventStream(storage.events_path)
        
        return run_id
    
    def get_run_context(self, run_id: str) -> Optional[RunContext]:
        """Get active run context or load from storage."""
        with self._lock:
            if run_id in self._active_runs:
                return self._active_runs[run_id]
        
        # Load from storage
        storage = self.storage_manager.get_run(run_id)
        if not storage:
            return None
        
        manifest = storage.load_manifest()
        if not manifest:
            return None
        
        profile = self.profile_manager.load_profile(manifest.profile_name)
        if not profile:
            return None
        
        events = EventStream(storage.events_path)
        
        return RunContext(
            run_id=run_id,
            storage=storage,
            manifest=manifest,
            profile=profile,
            events=events,
            parameters=manifest.parameters.copy()
        )
    
    def start_run(self, run_id: str) -> bool:
        """Start or resume a run."""
        ctx = self.get_run_context(run_id)
        if not ctx:
            return False
        
        with self._lock:
            if ctx.is_running:
                return True
            
            # Create SSH client with event callbacks (for target commands)
            def on_connect():
                event = ctx.events.append(EventType.CONNECTION_ESTABLISHED, {
                    'host': ctx.profile.connection.host
                })
                self._notify_event(run_id, event)
            
            def on_disconnect(reason):
                event = ctx.events.append(EventType.CONNECTION_LOST, {'reason': reason})
                self._notify_event(run_id, event)
            
            def on_retry(attempt, error):
                event = ctx.events.append(EventType.CONNECTION_RETRY, {
                    'attempt': attempt,
                    'error': error
                })
                self._notify_event(run_id, event)
            
            conn = ctx.profile.connection
            ctx.ssh = SSHClientWrapper(
                ConnectionConfig(
                    host=conn.host,
                    port=conn.port,
                    user=conn.user,
                    key_file=conn.key_file,
                    password=conn.password,
                    timeout=conn.timeout
                ),
                on_connect=on_connect,
                on_disconnect=on_disconnect,
                on_retry=on_retry
            )
            
            # Update status
            was_paused = ctx.manifest.status == RunStatus.PAUSED.value
            ctx.manifest.status = RunStatus.RUNNING.value
            if not ctx.manifest.started_at:
                ctx.manifest.started_at = datetime.now(timezone.utc).isoformat()
            ctx.storage.save_manifest(ctx.manifest)
            
            if was_paused:
                event = ctx.events.append(EventType.RUN_RESUMED, {})
            else:
                event = ctx.events.append(EventType.RUN_STARTED, {})
            self._notify_event(run_id, event)
            
            ctx.is_running = True
            ctx.is_paused = False
            self._active_runs[run_id] = ctx
            
            return True
    
    def pause_run(self, run_id: str) -> bool:
        """Pause an active run."""
        with self._lock:
            ctx = self._active_runs.get(run_id)
            if not ctx or not ctx.is_running:
                return False
            
            # Stop all background collectors
            for coll_name in list(ctx.collectors.keys()):
                self.stop_collector(run_id, coll_name)
            
            ctx.is_running = False
            ctx.is_paused = True
            ctx.manifest.status = RunStatus.PAUSED.value
            ctx.storage.save_manifest(ctx.manifest)
            event = ctx.events.append(EventType.RUN_PAUSED, {})
            self._notify_event(run_id, event)
            
            return True
    
    def complete_run(self, run_id: str) -> bool:
        """Mark a run as completed."""
        with self._lock:
            ctx = self._active_runs.get(run_id)
            if not ctx:
                ctx = self.get_run_context(run_id)
                if not ctx:
                    return False
            
            # Stop all background collectors
            for coll_name in list(ctx.collectors.keys()):
                self.stop_collector(run_id, coll_name)
            
            # Disconnect SSH
            if ctx.ssh:
                ctx.ssh.disconnect()
            
            ctx.is_running = False
            ctx.manifest.status = RunStatus.COMPLETED.value
            ctx.manifest.completed_at = datetime.now(timezone.utc).isoformat()
            ctx.storage.save_manifest(ctx.manifest)
            event = ctx.events.append(EventType.RUN_COMPLETED, {})
            self._notify_event(run_id, event)
            
            if run_id in self._active_runs:
                del self._active_runs[run_id]
            
            return True
    
    def set_parameter(
        self, 
        run_id: str, 
        name: str, 
        value: str,
        user: Optional[Dict[str, str]] = None
    ) -> bool:
        """Set a parameter value for a run."""
        ctx = self.get_run_context(run_id)
        if not ctx:
            return False
        
        ctx.parameters[name] = value
        ctx.manifest.parameters[name] = value
        ctx.storage.save_manifest(ctx.manifest)
        event = ctx.events.append(EventType.PARAMETER_SET, {'name': name, 'value': value}, user=user)
        self._notify_event(run_id, event)
        return True
    
    def execute_command(
        self, 
        run_id: str, 
        command_name: str,
        user: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Execute a single command by name.
        Runs on host or target based on command definition.
        Returns results dict with success status and outputs.
        
        Args:
            run_id: Run identifier
            command_name: Name of command to execute
            user: Optional user info {username, color} who triggered the command
        """
        ctx = self._active_runs.get(run_id)
        if not ctx or not ctx.is_running:
            return {'success': False, 'error': 'Run not active'}
        
        cmd_def = ctx.profile.commands.get(command_name)
        if not cmd_def:
            return {'success': False, 'error': f'Command not found: {command_name}'}
        
        # Substitute parameters first so we can log the actual command
        cmd = substitute_parameters(cmd_def.command, ctx.parameters)
        
        # Log command start with actual command and user info
        event = ctx.events.append(EventType.COMMAND_STARTED, {
            'command_name': command_name,
            'command': cmd,
            'run_location': cmd_def.run,
            'description': cmd_def.description
        }, user=user)
        self._notify_event(run_id, event)
        
        # Execute on host or target
        if cmd_def.run == 'target':
            # Ensure SSH connection
            if not ctx.ssh.is_connected:
                if not ctx.ssh.connect():
                    event = ctx.events.append(EventType.COMMAND_FAILED, {
                        'command_name': command_name,
                        'error': 'SSH connection failed'
                    })
                    self._notify_event(run_id, event)
                    return {'success': False, 'error': 'SSH connection failed'}
            
            result = ctx.ssh.execute(cmd, timeout=cmd_def.timeout)
        else:
            # Execute on host
            result = execute_host_command(cmd, timeout=cmd_def.timeout)
        
        cmd_result = {
            'command_name': command_name,
            'command': cmd,
            'run_location': cmd_def.run,
            'exit_code': result.exit_code,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'duration': result.duration
        }
        
        success = result.success
        
        if success:
            event = ctx.events.append(EventType.COMMAND_COMPLETED, cmd_result)
        else:
            event = ctx.events.append(EventType.COMMAND_FAILED, cmd_result)
        
        self._notify_event(run_id, event)
        
        # Pull artifacts (only for target commands with artifacts)
        artifacts = []
        if cmd_def.run == 'target' and cmd_def.artifacts:
            for artifact_template in cmd_def.artifacts:
                remote_path = substitute_parameters(artifact_template, ctx.parameters)
                
                event = ctx.events.append(EventType.ARTIFACT_PULL_STARTED, {'remote_path': remote_path})
                self._notify_event(run_id, event)
                
                # Create temp local path
                temp_path = os.path.join(ctx.storage.artifacts_dir, 
                                         f"_temp_{os.path.basename(remote_path)}")
                
                pull_success, error = ctx.ssh.get_file(remote_path, temp_path)
                
                if pull_success:
                    # Move to proper location in storage
                    local_path = ctx.storage.add_artifact(temp_path, remote_path)
                    os.remove(temp_path)
                    
                    artifact_info = {
                        'remote_path': remote_path,
                        'local_path': local_path,
                        'command': command_name
                    }
                    ctx.manifest.artifacts.append(artifact_info)
                    ctx.storage.save_manifest(ctx.manifest)
                    
                    event = ctx.events.append(EventType.ARTIFACT_PULLED, artifact_info)
                    self._notify_event(run_id, event)
                    artifacts.append(artifact_info)
                else:
                    event = ctx.events.append(EventType.ARTIFACT_PULL_FAILED, {
                        'remote_path': remote_path,
                        'error': error
                    })
                    self._notify_event(run_id, event)
        
        return {
            'success': success,
            'command_name': command_name,
            'run_location': cmd_def.run,
            'exit_code': result.exit_code,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'duration': result.duration,
            'artifacts': artifacts
        }
    
    def start_collector(self, run_id: str, collector_name: str) -> bool:
        """Start a background collector."""
        ctx = self._active_runs.get(run_id)
        if not ctx or not ctx.is_running:
            return False
        
        if collector_name in ctx.collectors and ctx.collectors[collector_name].running:
            return True
        
        coll_def = ctx.profile.background_collectors.get(collector_name)
        if not coll_def:
            return False
        
        # For target collectors, ensure SSH is connected
        if coll_def.run == 'target':
            if not ctx.ssh.is_connected:
                if not ctx.ssh.connect():
                    return False
        
        stop_event = threading.Event()
        collector = BackgroundCollector(
            name=collector_name,
            definition=coll_def,
            stop_event=stop_event
        )
        
        def collector_loop():
            event = ctx.events.append(EventType.COLLECTOR_STARTED, {
                'collector': collector_name,
                'run_location': coll_def.run
            })
            self._notify_event(run_id, event)
            
            while not stop_event.is_set():
                cmd = substitute_parameters(coll_def.command, ctx.parameters)
                
                if coll_def.run == 'target':
                    result = ctx.ssh.execute(cmd, timeout=coll_def.timeout)
                else:
                    result = execute_host_command(cmd, timeout=coll_def.timeout)
                
                if result.success:
                    event = ctx.events.append(EventType.COLLECTOR_OUTPUT, {
                        'collector': collector_name,
                        'stdout': result.stdout,
                        'stderr': result.stderr
                    })
                else:
                    event = ctx.events.append(EventType.COLLECTOR_ERROR, {
                        'collector': collector_name,
                        'error': result.stderr or 'Command failed'
                    })
                
                self._notify_event(run_id, event)
                
                stop_event.wait(coll_def.interval)
            
            event = ctx.events.append(EventType.COLLECTOR_STOPPED, {'collector': collector_name})
            self._notify_event(run_id, event)
        
        thread = threading.Thread(target=collector_loop, daemon=True)
        collector.thread = thread
        collector.running = True
        ctx.collectors[collector_name] = collector
        thread.start()
        
        return True
    
    def stop_collector(self, run_id: str, collector_name: str) -> bool:
        """Stop a background collector."""
        ctx = self._active_runs.get(run_id)
        if not ctx:
            return False
        
        collector = ctx.collectors.get(collector_name)
        if not collector or not collector.running:
            return False
        
        collector.stop_event.set()
        collector.running = False
        
        return True
    
    def add_note(
        self, 
        run_id: str, 
        note: str,
        user: Optional[Dict[str, str]] = None
    ) -> bool:
        """Add an operator note to a run."""
        ctx = self.get_run_context(run_id)
        if not ctx:
            return False
        
        event = ctx.events.append(EventType.NOTE, {'text': note}, user=user)
        self._notify_event(run_id, event)
        return True
    
    def get_events(self, run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        """Get events for a run, optionally after a sequence number."""
        ctx = self.get_run_context(run_id)
        if not ctx:
            return []
        
        events = []
        for e in ctx.events.iter_events(after_seq):
            event_dict = {
                'seq': e.seq,
                'timestamp': e.timestamp,
                'type': e.event_type,
                'data': e.data
            }
            if e.user:
                event_dict['user'] = e.user
            events.append(event_dict)
        
        return events
    
    def export_run(self, run_id: str) -> Optional[str]:
        """Create a zip archive of a run. Returns archive path."""
        storage = self.storage_manager.get_run(run_id)
        if not storage:
            return None
        return storage.create_archive()
    
    def delete_run(self, run_id: str) -> bool:
        """Delete a run."""
        with self._lock:
            if run_id in self._active_runs:
                # Stop everything first
                ctx = self._active_runs[run_id]
                for coll_name in list(ctx.collectors.keys()):
                    self.stop_collector(run_id, coll_name)
                if ctx.ssh:
                    ctx.ssh.disconnect()
                del self._active_runs[run_id]
        
        return self.storage_manager.delete_run(run_id)
