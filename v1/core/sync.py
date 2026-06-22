"""
Real-time synchronization for multi-user experiments.
Uses Flask-SocketIO for WebSocket communication.
"""

import json
from datetime import datetime, timezone
from typing import Dict, Set, Optional, Any
from dataclasses import dataclass, field
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask import request


@dataclass
class ConnectedUser:
    """Represents a connected user session."""
    sid: str  # Socket session ID
    username: str
    color: str
    connected_at: str
    current_run: Optional[str] = None


class SyncManager:
    """
    Manages real-time synchronization between multiple clients.
    Each run is a 'room' that users can join/leave.
    """
    
    # Predefined colors for users
    USER_COLORS = [
        '#58a6ff',  # blue
        '#3fb950',  # green
        '#f85149',  # red
        '#d29922',  # yellow
        '#a371f7',  # purple
        '#f778ba',  # pink
        '#79c0ff',  # light blue
        '#7ee787',  # light green
    ]
    
    def __init__(self, socketio: SocketIO):
        self.socketio = socketio
        self.users: Dict[str, ConnectedUser] = {}  # sid -> user
        self.run_users: Dict[str, Set[str]] = {}   # run_id -> set of sids
        self._color_index = 0
        
        # Register socket events
        self._register_events()
    
    def _get_next_color(self) -> str:
        """Get next color for a new user."""
        color = self.USER_COLORS[self._color_index % len(self.USER_COLORS)]
        self._color_index += 1
        return color
    
    def _register_events(self):
        """Register SocketIO event handlers."""
        
        @self.socketio.on('connect')
        def handle_connect():
            """Handle new client connection."""
            sid = request.sid
            # Default username until they set one
            username = f"User-{sid[:6]}"
            
            user = ConnectedUser(
                sid=sid,
                username=username,
                color=self._get_next_color(),
                connected_at=datetime.now(timezone.utc).isoformat()
            )
            self.users[sid] = user
            
            emit('user_info', {
                'sid': sid,
                'username': user.username,
                'color': user.color
            })
            
            print(f"[Sync] User connected: {username} ({sid})")
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            """Handle client disconnection."""
            sid = request.sid
            user = self.users.get(sid)
            
            if user:
                # Leave any run rooms
                if user.current_run:
                    self._leave_run(sid, user.current_run)
                
                del self.users[sid]
                print(f"[Sync] User disconnected: {user.username}")
        
        @self.socketio.on('set_username')
        def handle_set_username(data):
            """Handle username change."""
            sid = request.sid
            user = self.users.get(sid)
            if user:
                old_name = user.username
                user.username = data.get('username', user.username)[:32]  # Limit length
                
                # Notify room if in a run
                if user.current_run:
                    self.socketio.emit('user_renamed', {
                        'sid': sid,
                        'old_username': old_name,
                        'username': user.username,
                        'color': user.color
                    }, room=user.current_run)
        
        @self.socketio.on('join_run')
        def handle_join_run(data):
            """Handle user joining a run room."""
            sid = request.sid
            run_id = data.get('run_id')
            
            if not run_id:
                return
            
            user = self.users.get(sid)
            if user:
                # Leave previous run if any
                if user.current_run and user.current_run != run_id:
                    self._leave_run(sid, user.current_run)
                
                # Join new run
                self._join_run(sid, run_id)
        
        @self.socketio.on('leave_run')
        def handle_leave_run(data):
            """Handle user leaving a run room."""
            sid = request.sid
            run_id = data.get('run_id')
            
            if run_id:
                self._leave_run(sid, run_id)
    
    def _join_run(self, sid: str, run_id: str):
        """Add user to a run room."""
        user = self.users.get(sid)
        if not user:
            return
        
        # Join socket room
        join_room(run_id, sid=sid)
        user.current_run = run_id
        
        # Track in run_users
        if run_id not in self.run_users:
            self.run_users[run_id] = set()
        self.run_users[run_id].add(sid)
        
        # Notify others in the room
        self.socketio.emit('user_joined', {
            'sid': sid,
            'username': user.username,
            'color': user.color,
            'users': self.get_run_users(run_id)
        }, room=run_id)
        
        print(f"[Sync] {user.username} joined run {run_id[:8]}...")
    
    def _leave_run(self, sid: str, run_id: str):
        """Remove user from a run room."""
        user = self.users.get(sid)
        if not user:
            return
        
        # Leave socket room
        leave_room(run_id, sid=sid)
        user.current_run = None
        
        # Remove from tracking
        if run_id in self.run_users:
            self.run_users[run_id].discard(sid)
            if not self.run_users[run_id]:
                del self.run_users[run_id]
        
        # Notify others
        self.socketio.emit('user_left', {
            'sid': sid,
            'username': user.username,
            'users': self.get_run_users(run_id)
        }, room=run_id)
        
        print(f"[Sync] {user.username} left run {run_id[:8]}...")
    
    def get_run_users(self, run_id: str) -> list:
        """Get list of users in a run."""
        users = []
        for sid in self.run_users.get(run_id, set()):
            user = self.users.get(sid)
            if user:
                users.append({
                    'sid': sid,
                    'username': user.username,
                    'color': user.color
                })
        return users
    
    def get_user_by_sid(self, sid: str) -> Optional[ConnectedUser]:
        """Get user by socket session ID."""
        return self.users.get(sid)
    
    def broadcast_event(self, run_id: str, event_type: str, data: dict):
        """Broadcast an event to all users in a run."""
        self.socketio.emit(event_type, data, room=run_id)
    
    def broadcast_new_event(self, run_id: str, event: dict):
        """Broadcast a new timeline event to all users in a run."""
        self.socketio.emit('new_event', event, room=run_id)
    
    def broadcast_status_change(self, run_id: str, status: str):
        """Broadcast run status change."""
        self.socketio.emit('status_changed', {'status': status}, room=run_id)
    
    def broadcast_command_executing(self, run_id: str, command_name: str, user: dict):
        """Broadcast that a command is being executed."""
        self.socketio.emit('command_executing', {
            'command_name': command_name,
            'user': user
        }, room=run_id)


# Global instance (set by app.py)
sync_manager: Optional[SyncManager] = None


def init_sync(socketio: SocketIO) -> SyncManager:
    """Initialize the sync manager."""
    global sync_manager
    sync_manager = SyncManager(socketio)
    return sync_manager


def get_sync_manager() -> Optional[SyncManager]:
    """Get the global sync manager instance."""
    return sync_manager
