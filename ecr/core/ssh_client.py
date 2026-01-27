"""
SSH/SCP client wrapper for ECR.
Handles remote command execution and file transfer with resilience.
"""

import os
import time
import threading
from typing import Callable, Optional, Tuple
from dataclasses import dataclass
import paramiko
from paramiko import SSHClient, AutoAddPolicy, SFTPClient


@dataclass
class CommandResult:
    """Result of a remote command execution."""
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


@dataclass
class ConnectionConfig:
    """SSH connection configuration."""
    host: str
    port: int = 22
    user: str = "root"
    key_file: Optional[str] = None
    password: Optional[str] = None
    timeout: int = 30
    retry_attempts: int = 3
    retry_delay: int = 5


class SSHClientWrapper:
    """
    SSH client with automatic reconnection and resilience.
    """
    
    def __init__(self, config: ConnectionConfig, 
                 on_connect: Optional[Callable[[], None]] = None,
                 on_disconnect: Optional[Callable[[str], None]] = None,
                 on_retry: Optional[Callable[[int, str], None]] = None):
        self.config = config
        self._client: Optional[SSHClient] = None
        self._sftp: Optional[SFTPClient] = None
        self._lock = threading.Lock()
        self._connected = False
        
        # Callbacks
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_retry = on_retry
    
    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None
    
    def connect(self) -> bool:
        """Establish SSH connection with retry logic."""
        with self._lock:
            for attempt in range(1, self.config.retry_attempts + 1):
                try:
                    self._client = SSHClient()
                    self._client.set_missing_host_key_policy(AutoAddPolicy())
                    
                    connect_kwargs = {
                        'hostname': self.config.host,
                        'port': self.config.port,
                        'username': self.config.user,
                        'timeout': self.config.timeout,
                    }
                    
                    if self.config.key_file:
                        key_path = os.path.expanduser(self.config.key_file)
                        connect_kwargs['key_filename'] = key_path
                    elif self.config.password:
                        connect_kwargs['password'] = self.config.password
                    
                    self._client.connect(**connect_kwargs)
                    self._connected = True
                    
                    if self._on_connect:
                        self._on_connect()
                    
                    return True
                    
                except Exception as e:
                    error_msg = str(e)
                    if self._on_retry and attempt < self.config.retry_attempts:
                        self._on_retry(attempt, error_msg)
                    
                    if attempt < self.config.retry_attempts:
                        time.sleep(self.config.retry_delay)
                    else:
                        self._connected = False
                        if self._on_disconnect:
                            self._on_disconnect(f"Failed after {attempt} attempts: {error_msg}")
                        return False
        
        return False
    
    def disconnect(self):
        """Close SSH connection."""
        with self._lock:
            if self._sftp:
                try:
                    self._sftp.close()
                except:
                    pass
                self._sftp = None
            
            if self._client:
                try:
                    self._client.close()
                except:
                    pass
                self._client = None
            
            self._connected = False
    
    def _ensure_connected(self) -> bool:
        """Ensure connection is active, reconnect if needed."""
        if not self._connected or not self._client:
            return self.connect()
        
        # Test connection
        try:
            transport = self._client.get_transport()
            if transport is None or not transport.is_active():
                if self._on_disconnect:
                    self._on_disconnect("Connection lost")
                return self.connect()
        except:
            return self.connect()
        
        return True
    
    def execute(self, command: str, timeout: Optional[int] = None) -> CommandResult:
        """
        Execute a remote command.
        Returns CommandResult with stdout, stderr, exit code, and timing.
        """
        with self._lock:
            if not self._ensure_connected():
                return CommandResult(
                    command=command,
                    exit_code=-1,
                    stdout="",
                    stderr="Connection failed",
                    start_time=time.time(),
                    end_time=time.time()
                )
            
            start_time = time.time()
            try:
                stdin, stdout, stderr = self._client.exec_command(
                    command, 
                    timeout=timeout or self.config.timeout
                )
                
                stdout_str = stdout.read().decode('utf-8', errors='replace')
                stderr_str = stderr.read().decode('utf-8', errors='replace')
                exit_code = stdout.channel.recv_exit_status()
                
                return CommandResult(
                    command=command,
                    exit_code=exit_code,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    start_time=start_time,
                    end_time=time.time()
                )
                
            except Exception as e:
                self._connected = False
                return CommandResult(
                    command=command,
                    exit_code=-1,
                    stdout="",
                    stderr=str(e),
                    start_time=start_time,
                    end_time=time.time()
                )
    
    def get_file(self, remote_path: str, local_path: str) -> Tuple[bool, str]:
        """
        Copy a file from remote to local.
        Returns (success, error_message).
        """
        with self._lock:
            if not self._ensure_connected():
                return False, "Connection failed"
            
            try:
                if self._sftp is None:
                    self._sftp = self._client.open_sftp()
                
                # Ensure local directory exists
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                self._sftp.get(remote_path, local_path)
                return True, ""
                
            except FileNotFoundError:
                return False, f"Remote file not found: {remote_path}"
            except PermissionError:
                return False, f"Permission denied: {remote_path}"
            except Exception as e:
                return False, str(e)
    
    def put_file(self, local_path: str, remote_path: str) -> Tuple[bool, str]:
        """
        Copy a file from local to remote.
        Returns (success, error_message).
        """
        with self._lock:
            if not self._ensure_connected():
                return False, "Connection failed"
            
            try:
                if self._sftp is None:
                    self._sftp = self._client.open_sftp()
                
                self._sftp.put(local_path, remote_path)
                return True, ""
                
            except Exception as e:
                return False, str(e)
    
    def file_exists(self, remote_path: str) -> bool:
        """Check if a remote file exists."""
        with self._lock:
            if not self._ensure_connected():
                return False
            
            try:
                if self._sftp is None:
                    self._sftp = self._client.open_sftp()
                
                self._sftp.stat(remote_path)
                return True
            except FileNotFoundError:
                return False
            except:
                return False
