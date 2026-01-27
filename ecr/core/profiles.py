"""
Target profile management for ECR.
Handles loading, validation, and parameter substitution for profiles.
"""

import os
import re
import yaml
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class CommandDefinition:
    """Definition of a command from a profile."""
    name: str
    description: str
    command: str
    run: str = "host"  # "host" (default) or "target"
    artifacts: List[str] = field(default_factory=list)
    timeout: int = 60


@dataclass
class CollectorDefinition:
    """Definition of a background collector from a profile."""
    name: str
    command: str
    run: str = "target"  # collectors typically run on target
    interval: int = 60
    timeout: int = 10


@dataclass
class ConnectionProfile:
    """Connection parameters for a target."""
    host: str
    port: int = 22
    user: str = "root"
    key_file: Optional[str] = None
    password: Optional[str] = None
    timeout: int = 30


@dataclass
class TargetProfile:
    """Complete target profile."""
    name: str
    description: str
    connection: ConnectionProfile
    commands: Dict[str, CommandDefinition]
    background_collectors: Dict[str, CollectorDefinition]
    filepath: str
    
    @classmethod
    def from_yaml(cls, filepath: str) -> 'TargetProfile':
        """Load a profile from a YAML file."""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        # Parse connection
        conn_data = data.get('connection', {})
        connection = ConnectionProfile(
            host=conn_data.get('host', 'localhost'),
            port=conn_data.get('port', 22),
            user=conn_data.get('user', 'root'),
            key_file=conn_data.get('key_file'),
            password=conn_data.get('password'),
            timeout=conn_data.get('timeout', 30)
        )
        
        # Parse commands
        commands = {}
        for cmd_name, cmd_data in data.get('commands', {}).items():
            commands[cmd_name] = CommandDefinition(
                name=cmd_name,
                description=cmd_data.get('description', ''),
                command=cmd_data.get('command', ''),
                run=cmd_data.get('run', 'host'),  # default to host
                artifacts=cmd_data.get('artifacts', []),
                timeout=cmd_data.get('timeout', 60)
            )
        
        # Parse background collectors
        collectors = {}
        for coll_name, coll_data in data.get('background_collectors', {}).items():
            collectors[coll_name] = CollectorDefinition(
                name=coll_name,
                command=coll_data.get('command', ''),
                run=coll_data.get('run', 'target'),  # default to target for collectors
                interval=coll_data.get('interval', 60),
                timeout=coll_data.get('timeout', 10)
            )
        
        return cls(
            name=data.get('name', os.path.basename(filepath)),
            description=data.get('description', ''),
            connection=connection,
            commands=commands,
            background_collectors=collectors,
            filepath=filepath
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert profile to dictionary for serialization."""
        return {
            'name': self.name,
            'description': self.description,
            'connection': {
                'host': self.connection.host,
                'port': self.connection.port,
                'user': self.connection.user,
                'key_file': self.connection.key_file,
                'timeout': self.connection.timeout
            },
            'commands': {
                name: {
                    'description': cmd.description,
                    'command': cmd.command,
                    'run': cmd.run,
                    'artifacts': cmd.artifacts,
                    'timeout': cmd.timeout
                }
                for name, cmd in self.commands.items()
            },
            'background_collectors': {
                name: {
                    'command': coll.command,
                    'run': coll.run,
                    'interval': coll.interval,
                    'timeout': coll.timeout
                }
                for name, coll in self.background_collectors.items()
            }
        }
    
    def to_yaml(self) -> str:
        """Convert profile to YAML string."""
        return yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)


class ProfileManager:
    """Manages loading and listing target profiles."""
    
    def __init__(self, profiles_dir: str):
        self.profiles_dir = profiles_dir
        os.makedirs(profiles_dir, exist_ok=True)
    
    def list_profiles(self) -> List[str]:
        """List all available profile names."""
        profiles = []
        for filename in os.listdir(self.profiles_dir):
            if filename.endswith(('.yaml', '.yml')):
                profiles.append(filename.rsplit('.', 1)[0])
        return sorted(profiles)
    
    def load_profile(self, name: str) -> Optional[TargetProfile]:
        """Load a profile by name."""
        for ext in ('.yaml', '.yml'):
            filepath = os.path.join(self.profiles_dir, name + ext)
            if os.path.exists(filepath):
                return TargetProfile.from_yaml(filepath)
        return None
    
    def save_profile(self, profile: TargetProfile) -> str:
        """Save a profile to disk."""
        filepath = os.path.join(self.profiles_dir, profile.name + '.yaml')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(profile.to_yaml())
        return filepath
    
    def delete_profile(self, name: str) -> bool:
        """Delete a profile by name."""
        for ext in ('.yaml', '.yml'):
            filepath = os.path.join(self.profiles_dir, name + ext)
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
        return False


def substitute_parameters(template: str, params: Dict[str, str]) -> str:
    """
    Substitute {param_name} placeholders in a template string.
    """
    def replacer(match):
        param_name = match.group(1)
        return params.get(param_name, match.group(0))
    
    return re.sub(r'\{(\w+)\}', replacer, template)


def extract_parameters(template: str) -> List[str]:
    """
    Extract parameter names from a template string.
    """
    return list(set(re.findall(r'\{(\w+)\}', template)))


def get_command_parameters(cmd: CommandDefinition) -> List[str]:
    """
    Get all parameter names used in a command and its artifacts.
    """
    params = set()
    params.update(extract_parameters(cmd.command))
    for artifact in cmd.artifacts:
        params.update(extract_parameters(artifact))
    return sorted(params)
