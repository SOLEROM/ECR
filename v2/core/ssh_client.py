"""
SSH client wrapper for ccflet.

  - **Jump-host** — open the roleB connection *through* the roleA transport via a
    `direct-tcpip` channel passed as `sock=` to the inner connect. Driven by
    `connection.via` ("user@host").
  - **Streaming exec** — `exec_stream()` yields lines on a *dedicated* channel.
    The blocking `execute()` holds `self._lock` for the whole command, so live
    `tail -F` streaming must never go through it.

One SSHClientWrapper is held per node target (roleA + roleB = 2), pooled across
actions by the orchestrator.
"""

import os
import time
import threading
from typing import Callable, Iterator, Optional, Tuple

import paramiko
from paramiko import SSHClient, AutoAddPolicy

from .result import CommandResult


class ConnectionConfig:
    """SSH connection configuration (incl. optional jump-host)."""

    def __init__(self, host: str, port: int = 22, user: str = "root",
                 key_file: Optional[str] = None, password: Optional[str] = None,
                 timeout: int = 5, retry_attempts: int = 3, retry_delay: int = 3,
                 jump_user: Optional[str] = None, jump_host: Optional[str] = None,
                 jump_port: int = 22):
        self.host = host
        self.port = port
        self.user = user
        self.key_file = key_file
        self.password = password
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.jump_user = jump_user
        self.jump_host = jump_host
        self.jump_port = jump_port

    @classmethod
    def from_profile_connection(cls, conn, **overrides):
        """Build from a profiles.Connection (already rendered). `via` → jump-host."""
        jump_user = jump_host = None
        if getattr(conn, "via", None):
            via = conn.via
            jump_user, _, jump_host = via.partition("@")
            if not jump_host:  # bare host, no user given
                jump_host, jump_user = jump_user, None
        cfg = cls(
            host=conn.host, port=conn.port, user=conn.user,
            key_file=conn.key_file, timeout=conn.timeout,
            jump_user=jump_user, jump_host=jump_host,
        )
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg


class SSHClientWrapper:
    """SSH client with reconnection, jump-host and streaming support."""

    def __init__(self, config: ConnectionConfig,
                 on_connect: Optional[Callable[[], None]] = None,
                 on_disconnect: Optional[Callable[[str], None]] = None,
                 on_retry: Optional[Callable[[int, str], None]] = None):
        self.config = config
        self._client: Optional[SSHClient] = None
        self._jump: Optional[SSHClient] = None
        self._sftp = None
        self._lock = threading.Lock()
        self._connected = False
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_retry = on_retry

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    def _connect_kwargs(self) -> dict:
        kwargs = {
            "hostname": self.config.host,
            "port": self.config.port,
            "username": self.config.user,
            "timeout": self.config.timeout,
            "allow_agent": True,
            "look_for_keys": True,
        }
        if self.config.key_file:
            kwargs["key_filename"] = os.path.expanduser(self.config.key_file)
        if self.config.password:
            kwargs["password"] = self.config.password
        return kwargs

    def _open_jump_channel(self):
        """Open a direct-tcpip channel through the jump host → returns a sock."""
        self._jump = SSHClient()
        self._jump.set_missing_host_key_policy(AutoAddPolicy())
        jkwargs = {
            "hostname": self.config.jump_host,
            "port": self.config.jump_port,
            "username": self.config.jump_user or self.config.user,
            "timeout": self.config.timeout,
            "allow_agent": True,
            "look_for_keys": True,
        }
        if self.config.key_file:
            jkwargs["key_filename"] = os.path.expanduser(self.config.key_file)
        self._jump.connect(**jkwargs)
        transport = self._jump.get_transport()
        return transport.open_channel(
            "direct-tcpip",
            (self.config.host, self.config.port),
            ("127.0.0.1", 0),
        )

    def connect(self) -> bool:
        with self._lock:
            for attempt in range(1, self.config.retry_attempts + 1):
                try:
                    self._client = SSHClient()
                    self._client.set_missing_host_key_policy(AutoAddPolicy())
                    kwargs = self._connect_kwargs()
                    if self.config.jump_host:
                        kwargs["sock"] = self._open_jump_channel()
                    self._client.connect(**kwargs)
                    self._connected = True
                    if self._on_connect:
                        self._on_connect()
                    return True
                except Exception as e:  # noqa: BLE001 — surfaced via callbacks
                    msg = str(e)
                    self._cleanup_jump()
                    if attempt < self.config.retry_attempts:
                        if self._on_retry:
                            self._on_retry(attempt, msg)
                        time.sleep(self.config.retry_delay)
                    else:
                        self._connected = False
                        if self._on_disconnect:
                            self._on_disconnect(f"Failed after {attempt} attempts: {msg}")
                        return False
        return False

    def _cleanup_jump(self):
        if self._jump:
            try:
                self._jump.close()
            except Exception:
                pass
            self._jump = None

    def disconnect(self):
        with self._lock:
            for obj in (self._sftp, self._client):
                if obj:
                    try:
                        obj.close()
                    except Exception:
                        pass
            self._sftp = None
            self._client = None
            self._cleanup_jump()
            self._connected = False

    def _ensure_connected(self) -> bool:
        if not self._connected or not self._client:
            return self.connect()
        try:
            transport = self._client.get_transport()
            if transport is None or not transport.is_active():
                if self._on_disconnect:
                    self._on_disconnect("Connection lost")
                return self.connect()
        except Exception:
            return self.connect()
        return True

    def execute(self, command: str, timeout: Optional[int] = None) -> CommandResult:
        """Run a command to completion; lock-serialized (never use for streaming)."""
        with self._lock:
            if not self._ensure_connected():
                return CommandResult(command, -1, "", "Connection failed",
                                     time.time(), time.time())
            start = time.time()
            try:
                _in, out, err = self._client.exec_command(
                    command, timeout=timeout or self.config.timeout
                )
                stdout = out.read().decode("utf-8", errors="replace")
                stderr = err.read().decode("utf-8", errors="replace")
                code = out.channel.recv_exit_status()
                return CommandResult(command, code, stdout, stderr, start, time.time())
            except Exception as e:  # noqa: BLE001
                self._connected = False
                return CommandResult(command, -1, "", str(e), start, time.time())

    def exec_stream(self, command: str, stop_event: Optional[threading.Event] = None
                    ) -> Iterator[str]:
        """
        Yield output lines from `command` on a **dedicated** channel. The
        connection is snapshotted under the lock (so a concurrent disconnect can't
        null `_client` mid-call), but the long read loop runs lock-free so it
        never blocks `execute()`. Terminates when the command exits or stop_event
        is set.
        """
        with self._lock:
            if not self._ensure_connected():
                yield "[stream] connection failed"
                return
            client = self._client
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            yield "[stream] transport not active"
            return
        chan = transport.open_session()
        chan.settimeout(0.5)
        chan.exec_command(command)
        buf = ""
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                if chan.recv_ready():
                    chunk = chan.recv(4096).decode("utf-8", errors="replace")
                    if not chunk:
                        break
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        yield line
                elif chan.exit_status_ready():
                    break
                else:
                    time.sleep(0.05)
        finally:
            if buf:
                yield buf
            try:
                chan.close()
            except Exception:
                pass

    def get_file(self, remote_path: str, local_path: str) -> Tuple[bool, str]:
        with self._lock:
            if not self._ensure_connected():
                return False, "Connection failed"
            try:
                if self._sftp is None:
                    self._sftp = self._client.open_sftp()
                os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
                self._sftp.get(remote_path, local_path)
                return True, ""
            except FileNotFoundError:
                return False, f"Remote file not found: {remote_path}"
            except Exception as e:  # noqa: BLE001
                return False, str(e)

    def put_file(self, local_path: str, remote_path: str) -> Tuple[bool, str]:
        with self._lock:
            if not self._ensure_connected():
                return False, "Connection failed"
            try:
                if self._sftp is None:
                    self._sftp = self._client.open_sftp()
                self._sftp.put(local_path, remote_path)
                return True, ""
            except Exception as e:  # noqa: BLE001
                return False, str(e)

    def file_exists(self, remote_path: str) -> bool:
        with self._lock:
            if not self._ensure_connected():
                return False
            try:
                if self._sftp is None:
                    self._sftp = self._client.open_sftp()
                self._sftp.stat(remote_path)
                return True
            except Exception:
                return False
