"""
Profile templates for ccflet.

A profile is a parameterized command catalog for one *role* (roleA or roleB):
  - connection : user/host/port/key_file/timeout (+ `via` for the roleB jump-host)
  - actions    : kind ∈ {transfer, exec, daemon, daemon_stop, daemon_status}
  - collectors : periodic exec + a named parser (status polling)
  - logs       : tailable paths for live streaming

`{param}` placeholders are rendered against the dict produced by
`fleet.Fleet.params(node, variant)`. Rendering is pure — it returns a new, fully
substituted copy and never mutates the template.
"""

import os
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, replace

import yaml

ACTION_KINDS = ("transfer", "exec", "daemon", "daemon_stop", "daemon_status")
TRANSFER_METHODS = ("rsync", "scp")


@dataclass(frozen=True)
class Action:
    name: str
    kind: str
    command: str = ""                 # exec / daemon
    method: Optional[str] = None      # transfer: rsync | scp
    src: Optional[str] = None         # transfer source (local)
    dst: Optional[str] = None         # transfer destination (remote)
    daemon: Optional[str] = None      # daemon name → pidfile/logfile key
    match: Optional[str] = None       # pgrep/pkill pattern
    prefer_systemd: Optional[str] = None  # systemd unit to prefer (e.g. serviceA)
    after: Optional[str] = None       # ordering hint (daemon name)
    timeout: int = 60


@dataclass(frozen=True)
class Collector:
    name: str
    command: str
    parser: str
    interval: float = 2.0
    timeout: int = 10


@dataclass(frozen=True)
class Connection:
    user: str = "root"
    host: str = "localhost"
    port: int = 22
    key_file: Optional[str] = None
    timeout: int = 5
    via: Optional[str] = None         # "user@host" jump-host (roleB through roleA)


@dataclass(frozen=True)
class Profile:
    name: str
    connection: Connection
    actions: Dict[str, Action]
    collectors: Dict[str, Collector]
    logs: Dict[str, str]
    filepath: Optional[str] = None

    def action(self, name: str) -> Optional[Action]:
        return self.actions.get(name)


# --- parameter substitution --------------------------------------------------
_PARAM_RE = re.compile(r"\{(\w+)\}")


def substitute(template: Optional[str], params: Dict[str, str]) -> Optional[str]:
    """Replace {param} in a string; unknown params are left verbatim."""
    if template is None:
        return None
    return _PARAM_RE.sub(lambda m: str(params.get(m.group(1), m.group(0))), template)


def extract_params(template: Optional[str]) -> List[str]:
    if not template:
        return []
    return sorted(set(_PARAM_RE.findall(template)))


def render_connection(conn: Connection, params: Dict[str, str]) -> Connection:
    return replace(
        conn,
        user=substitute(conn.user, params),
        host=substitute(conn.host, params),
        key_file=substitute(conn.key_file, params),
        via=substitute(conn.via, params),
    )


def render_action(action: Action, params: Dict[str, str]) -> Action:
    return replace(
        action,
        command=substitute(action.command, params) or "",
        src=substitute(action.src, params),
        dst=substitute(action.dst, params),
        match=substitute(action.match, params),
    )


def render_collector(coll: Collector, params: Dict[str, str]) -> Collector:
    return replace(coll, command=substitute(coll.command, params))


def render_logs(logs: Dict[str, str], params: Dict[str, str]) -> Dict[str, str]:
    return {k: substitute(v, params) for k, v in logs.items()}


# --- loading -----------------------------------------------------------------
def _action_from_dict(name: str, d: Dict[str, Any]) -> Action:
    kind = d.get("kind")
    if kind not in ACTION_KINDS:
        raise ValueError(f"action {name!r}: invalid kind {kind!r}; expected {ACTION_KINDS}")
    method = d.get("method")
    if kind == "transfer" and method not in TRANSFER_METHODS:
        raise ValueError(f"action {name!r}: transfer needs method in {TRANSFER_METHODS}")
    return Action(
        name=name,
        kind=kind,
        command=d.get("command", ""),
        method=method,
        src=d.get("src"),
        dst=d.get("dst"),
        daemon=d.get("name"),
        match=d.get("match"),
        prefer_systemd=d.get("prefer_systemd"),
        after=d.get("after"),
        timeout=int(d.get("timeout", 60)),
    )


def profile_from_dict(data: Dict[str, Any], name: str = "", filepath: Optional[str] = None) -> Profile:
    conn_data = data.get("connection", {}) or {}
    connection = Connection(
        user=conn_data.get("user", "root"),
        host=conn_data.get("host", "localhost"),
        port=int(conn_data.get("port", 22)),
        key_file=conn_data.get("key_file"),
        timeout=int(conn_data.get("timeout", 5)),
        via=conn_data.get("via"),
    )
    actions = {
        n: _action_from_dict(n, d) for n, d in (data.get("actions", {}) or {}).items()
    }
    collectors = {}
    for n, d in (data.get("collectors", {}) or {}).items():
        collectors[n] = Collector(
            name=n,
            command=d.get("command", ""),
            parser=d.get("parser", ""),
            interval=float(d.get("interval", 2.0)),
            timeout=int(d.get("timeout", 10)),
        )
    logs = dict(data.get("logs", {}) or {})
    return Profile(
        name=data.get("name", name),
        connection=connection,
        actions=actions,
        collectors=collectors,
        logs=logs,
        filepath=filepath,
    )


def load_profile(filepath: str) -> Profile:
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    base = os.path.splitext(os.path.basename(filepath))[0]
    return profile_from_dict(data, name=base, filepath=filepath)


class ProfileManager:
    """Loads role profiles (roleA.yaml, roleB.yaml) from a directory."""

    def __init__(self, profiles_dir: str):
        self.profiles_dir = profiles_dir
        self._cache: Dict[str, Profile] = {}

    def invalidate(self):
        """Drop cached profiles so the next ``load`` re-reads from disk (D8 reload)."""
        self._cache.clear()

    def load(self, name: str) -> Optional[Profile]:
        if name in self._cache:
            return self._cache[name]
        for ext in (".yaml", ".yml"):
            fp = os.path.join(self.profiles_dir, name + ext)
            if os.path.exists(fp):
                prof = load_profile(fp)
                self._cache[name] = prof
                return prof
        return None

    def list(self) -> List[str]:
        out = []
        if os.path.isdir(self.profiles_dir):
            for fn in os.listdir(self.profiles_dir):
                if fn.endswith((".yaml", ".yml")):
                    out.append(os.path.splitext(fn)[0])
        return sorted(out)
