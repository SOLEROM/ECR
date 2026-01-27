"""
ECR Core - Experiment Control & Record Engine
"""

from .events import EventStream, EventType, Event
from .ssh_client import SSHClientWrapper, ConnectionConfig, CommandResult
from .profiles import (
    TargetProfile, CommandDefinition, CollectorDefinition,
    ProfileManager, substitute_parameters, get_command_parameters
)
from .storage import RunStorage, StorageManager, RunManifest, RunStatus
from .engine import ExperimentEngine, RunContext

__all__ = [
    'EventStream', 'EventType', 'Event',
    'SSHClientWrapper', 'ConnectionConfig', 'CommandResult',
    'TargetProfile', 'CommandDefinition', 'CollectorDefinition',
    'ProfileManager', 'substitute_parameters', 'get_command_parameters',
    'RunStorage', 'StorageManager', 'RunManifest', 'RunStatus',
    'ExperimentEngine', 'RunContext'
]
