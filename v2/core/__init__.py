"""ccflet core — SSH command & control engine for a generic node fleet."""

from .result import CommandResult
from .events import EventStream, EventType, Event
from .storage import (
    SessionManager, SessionStorage, SessionManifest, SessionStatus, now_iso,
)
from .sync import SyncManager, init_sync, get_sync_manager, FLEET_ROOM, stream_room
from .fleet import (
    Fleet, Node, FleetDefaults, load_fleet, fleet_from_dict,
)
from .profiles import (
    Profile, Action, Collector, Connection, ProfileManager,
    load_profile, profile_from_dict, substitute, render_action, render_connection,
)
from . import supervisor
from . import status
from . import transfer
from .commands import Command, CommandCatalog, commands_from_dict
from .networks import Networks, NetLink, load_networks, networks_from_dict
from .states import (
    StateRegistry, Indicator, STATE_COLORS,
    cmd_states_from_dict, state_file_from_dict,
)
from .state_monitor import StateMonitor, ping_once
from .config_store import ConfigStore, ConfigRoot, default_roots, validate_text
from .orchestrator import Orchestrator, ActionResult, ConnectionPool
from .streaming import StreamManager

__all__ = [
    "CommandResult",
    "EventStream", "EventType", "Event",
    "SessionManager", "SessionStorage", "SessionManifest", "SessionStatus", "now_iso",
    "SyncManager", "init_sync", "get_sync_manager", "FLEET_ROOM", "stream_room",
    "Fleet", "Node", "FleetDefaults", "load_fleet", "fleet_from_dict",
    "Profile", "Action", "Collector", "Connection", "ProfileManager",
    "load_profile", "profile_from_dict", "substitute", "render_action",
    "render_connection",
    "supervisor", "status", "transfer",
    "Command", "CommandCatalog", "commands_from_dict",
    "Networks", "NetLink", "load_networks", "networks_from_dict",
    "StateRegistry", "Indicator", "STATE_COLORS",
    "cmd_states_from_dict", "state_file_from_dict",
    "StateMonitor", "ping_once",
    "ConfigStore", "ConfigRoot", "default_roots", "validate_text",
    "Orchestrator", "ActionResult", "ConnectionPool",
    "StreamManager",
]
