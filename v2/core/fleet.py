"""
Fleet inventory model and per-node parameter derivation for ccFleet.

This is the single source of truth for a node's `$ID`, host, subnet, the
fleet-wide `algo` parameter and the per-node **variant**. The variant (A/B) is
runtime state: each node carries its own, so a fleet can run mixed variants by
group. A variant selects a configurable set of derived parameters
(``VAR_ADDR`` / ``VAR_LAUNCHER`` / ``VAR_FLAG``) defined in ``defaults.variants`` —
nothing about a variant is hard-coded here, so the same mechanism fits any project
that needs two (or more) per-node profiles.

Derived parameters are computed here and never hand-typed — they are what profile
templates substitute into action/collector/log commands.
"""

import os
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, replace

import yaml

# Identifiers substituted into remote/local shell commands (directly and via the
# operator command catalog) must be bare tokens — allowlist-by-shape blocks shell
# metacharacter injection (no spaces/;/$/backticks/…). `algo` is checked at set time
# (Fleet.set_algo); name/host/subnet are checked when the inventory loads.
ALGO_RE = re.compile(r"^[A-Za-z0-9_-]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")            # fleet + node names
HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")          # host / subnet (IP or hostname)

VARIANTS = ("A", "B")
DEFAULT_ROLEB_SUFFIX = ".2"

# Built-in variant parameter sets — pure placeholders, overridden by
# `defaults.variants` in the fleet YAML. `addr` may contain the literal token
# `{SUBNET}`, replaced per node with that node's subnet, so variant B can derive a
# per-node address while variant A uses a fixed one.
DEFAULT_VARIANTS: Dict[str, Dict[str, str]] = {
    "A": {"addr": "10.0.0.255", "launcher": "variantA.run", "flag": ""},
    "B": {"addr": "{SUBNET}.255", "launcher": "variantB.run", "flag": "--variant-flag"},
}


@dataclass(frozen=True)
class FleetDefaults:
    """Fleet-wide defaults; a Node may override the per-node-overridable ones."""
    variant: str = "A"         # default variant for nodes that don't set one
    algo: str = "default"      # fleet-wide strategy/parameter token
    roleA_user: str = "user"
    roleB_user: str = "root"
    key_file: str = "~/.ssh/id_rsa"
    deploy_root: str = "/srv/ccfleet/roleA"
    ssh_opts: str = "-o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=5"
    stagger: float = 0.5       # seconds between node starts in a fleet bring-up
    roleB_host_suffix: str = DEFAULT_ROLEB_SUFFIX  # HOST_B = <subnet><suffix>
    variants: Dict[str, Dict[str, str]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_VARIANTS.items()})


@dataclass(frozen=True)
class Node:
    """One fleet node. Connection/algo/variant fields default to None → inherit fleet."""
    name: str
    id: int
    host: str                   # roleA host (the primary, directly reachable host)
    subnet: str                 # per-node subnet token (roleB host + VAR_ADDR derive from it)
    variant: Optional[str] = None   # A/B (per-node); None → defaults.variant
    algo: Optional[str] = None
    roleA_user: Optional[str] = None
    roleB_user: Optional[str] = None
    key_file: Optional[str] = None
    deploy_root: Optional[str] = None
    ssh_opts: Optional[str] = None


@dataclass(frozen=True)
class Group:
    """A named, operator-defined subset of the fleet used purely for *selection*
    on the dashboard (e.g. ``front: [node1, node2, node3]``). Order is preserved.
    The name is display-only — it reaches the UI and audit, never a shell."""
    name: str
    nodes: tuple = ()


class Fleet:
    """The fleet inventory + runtime variant/algo selection."""

    def __init__(self, name: str, defaults: FleetDefaults, nodes: List[Node],
                 groups: Optional[List["Group"]] = None):
        self.name = name
        self.defaults = defaults
        self.nodes = nodes
        self._by_name = {n.name: n for n in nodes}
        # operator-defined selection groups (dashboard-only, see groups_as_list)
        self.groups = list(groups or [])
        # runtime selection (mutable). The variant is **per-node**: each node is
        # toggled from its own card. `default_variant` is the fallback for nodes that
        # don't set one (and for new nodes added by a reload). `algo` stays fleet-wide.
        self.default_variant = defaults.variant
        self.node_variants: Dict[str, str] = {
            n.name: (n.variant if n.variant in VARIANTS else defaults.variant)
            for n in nodes
        }
        self.algo = defaults.algo

    # ---- lookup ----------------------------------------------------------
    def get(self, name: str) -> Optional[Node]:
        return self._by_name.get(name)

    def names(self) -> List[str]:
        return [n.name for n in self.nodes]

    def resolve(self, name_or_node) -> Optional[Node]:
        if isinstance(name_or_node, Node):
            return name_or_node
        return self.get(name_or_node)

    def groups_as_list(self) -> List[Dict[str, Any]]:
        """The selection groups as plain JSON for the dashboard's Select line.

        Members are filtered to nodes that still exist, so a stale reference (e.g.
        a node removed after the group was authored) never produces a dead button.
        """
        out = []
        for g in self.groups:
            members = [n for n in g.nodes if n in self._by_name]
            out.append({"name": g.name, "nodes": members})
        return out

    # ---- runtime selection ----------------------------------------------
    def node_variant(self, name_or_node) -> str:
        """The live variant for one node (per-node)."""
        node = self.resolve(name_or_node)
        name = node.name if node else name_or_node
        return self.node_variants.get(name, self.default_variant)

    def set_node_variant(self, name: str, variant: str):
        """Set one node's live variant. Validates the token and that the node exists."""
        if variant not in VARIANTS:
            raise ValueError(f"invalid variant {variant!r}; expected one of {VARIANTS}")
        if name not in self._by_name:
            raise ValueError(f"unknown node {name!r}")
        self.node_variants[name] = variant

    def set_variant(self, variant: str):
        """Bulk helper — set **every** node to `variant` (and the default). Not exposed
        in the UI (variant is per-node), but kept for tests and any future bulk path."""
        if variant not in VARIANTS:
            raise ValueError(f"invalid variant {variant!r}; expected one of {VARIANTS}")
        self.default_variant = variant
        for name in self.node_variants:
            self.node_variants[name] = variant

    def set_algo(self, algo: str):
        if not algo or not ALGO_RE.match(algo):
            raise ValueError(f"invalid algo {algo!r}; must match {ALGO_RE.pattern}")
        self.algo = algo

    # ---- parameter derivation -------------------------------------------
    def params(self, node: Node, variant: Optional[str] = None,
               algo: Optional[str] = None) -> Dict[str, str]:
        """
        Compute the full substitution dict for a node under the node's current (or
        explicitly given) variant/algo. Pure: same inputs → same output.
        """
        variant = variant or self.node_variant(node)
        if variant not in VARIANTS:
            raise ValueError(f"invalid variant {variant!r}")
        d = self.defaults
        algo = algo or node.algo or self.algo or d.algo
        roleA_user = node.roleA_user or d.roleA_user
        roleB_user = node.roleB_user or d.roleB_user
        key_file = node.key_file or d.key_file
        deploy_root = node.deploy_root or d.deploy_root
        ssh_opts = node.ssh_opts or d.ssh_opts
        vcfg = d.variants.get(variant, {})
        var_addr = str(vcfg.get("addr", "")).replace("{SUBNET}", node.subnet)
        return {
            # derived (UPPER) — consumed by action/collector commands
            "ID": str(node.id),
            "HOST_A": node.host,
            "SUBNET": node.subnet,
            "HOST_B": f"{node.subnet}{d.roleB_host_suffix}",
            "VAR_ADDR": var_addr,
            "VAR_LAUNCHER": str(vcfg.get("launcher", "")),
            "VAR_FLAG": str(vcfg.get("flag", "")),
            "ALGO": algo,
            "VARIANT": variant,
            "DEPLOY_ROOT": deploy_root,
            # connection-template params (lower) — consumed by profile connection
            "name": node.name,
            "roleA_user": roleA_user,
            "roleB_user": roleB_user,
            "key_file": key_file,
            "ssh_opts": ssh_opts,
            "deploy_root": deploy_root,
        }

    # ---- hot reload (config over code) ----------------------------------
    def reload_from_dict(self, raw: Dict[str, Any], source: str = "<reload>") -> "Fleet":
        """Re-load inventory + defaults from a parsed YAML dict, **in place**.

        Validates through `fleet_from_dict` first (raises ValueError on a bad file,
        so a broken edit never half-applies). The instance is mutated in place
        because the orchestrator, the SSH-client factory closure and the mock state
        all hold this same `Fleet` reference — replacing the object would orphan
        them. The live per-node `variant` and the `algo` selection are **preserved**
        (they are operator runtime state set from the dashboard, not file defaults):
        a surviving node keeps its live variant; a newly-added node takes its
        configured `variant:` (or the new default); a removed node drops out.
        """
        fresh = fleet_from_dict(raw, source=source)
        self.name = fresh.name
        self.defaults = fresh.defaults
        self.nodes = fresh.nodes
        self._by_name = {n.name: n for n in fresh.nodes}
        self.groups = fresh.groups
        self.default_variant = fresh.defaults.variant
        self.node_variants = {
            n.name: (self.node_variants.get(n.name)          # surviving → keep live variant
                     or (n.variant if n.variant in VARIANTS else self.default_variant))
            for n in fresh.nodes
        }
        if not self.algo or not ALGO_RE.match(self.algo):
            self.algo = fresh.defaults.algo
        return self

    # ---- serialization ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        dd = self.defaults
        d = {
            "fleet": {
                "name": self.name,
                "defaults": {
                    "variant": self.default_variant, "algo": self.algo,
                    "roleA_user": dd.roleA_user, "roleB_user": dd.roleB_user,
                    "key_file": dd.key_file, "deploy_root": dd.deploy_root,
                    "ssh_opts": dd.ssh_opts, "stagger": dd.stagger,
                    "roleB_host_suffix": dd.roleB_host_suffix,
                    "variants": {k: dict(v) for k, v in dd.variants.items()},
                },
                "nodes": [
                    {k: v for k, v in {
                        # the *live* per-node variant is recorded so a session snapshot
                        # captures exactly what each node was running.
                        "name": n.name, "id": n.id, "host": n.host,
                        "subnet": n.subnet, "variant": self.node_variant(n), "algo": n.algo,
                        "roleA_user": n.roleA_user, "roleB_user": n.roleB_user,
                        "key_file": n.key_file, "deploy_root": n.deploy_root,
                        "ssh_opts": n.ssh_opts,
                    }.items() if v is not None}
                    for n in self.nodes
                ],
            }
        }
        if self.groups:
            d["fleet"]["groups"] = {g.name: list(g.nodes) for g in self.groups}
        return d

    def to_yaml(self) -> str:
        return yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)


def _node_from_dict(d: Dict[str, Any]) -> Node:
    if "name" not in d or "id" not in d or "host" not in d or "subnet" not in d:
        raise ValueError(f"node missing required field(s) name/id/host/subnet: {d!r}")
    name, host, subnet = str(d["name"]), str(d["host"]), str(d["subnet"])
    if not NAME_RE.match(name):
        raise ValueError(f"node name {name!r} must match {NAME_RE.pattern} (bare token)")
    if not HOST_RE.match(host):
        raise ValueError(f"node {name!r}: host {host!r} is not a valid host token")
    if not HOST_RE.match(subnet):
        raise ValueError(f"node {name!r}: subnet {subnet!r} is not a valid host token")
    variant = d.get("variant")
    if variant is not None and variant not in VARIANTS:
        raise ValueError(f"node {name!r}: invalid variant {variant!r}; expected one of {VARIANTS}")
    return Node(
        name=name,
        id=int(d["id"]),
        host=host,
        subnet=subnet,
        variant=variant,
        algo=d.get("algo"),
        roleA_user=d.get("roleA_user"),
        roleB_user=d.get("roleB_user"),
        key_file=d.get("key_file"),
        deploy_root=d.get("deploy_root"),
        ssh_opts=d.get("ssh_opts"),
    )


def _variants_from_dict(raw: Any) -> Dict[str, Dict[str, str]]:
    """Merge an optional ``defaults.variants`` block over the built-in placeholders.

    Each variant is a small mapping (``addr``/``launcher``/``flag``); missing
    variants or sub-keys fall back to ``DEFAULT_VARIANTS`` so a partial edit is safe.
    """
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("'variants' must be a mapping of variant -> {addr,launcher,flag}")
    out: Dict[str, Dict[str, str]] = {}
    for k in VARIANTS:
        v = dict(DEFAULT_VARIANTS.get(k, {}))
        rv = raw.get(k) or {}
        if not isinstance(rv, dict):
            raise ValueError(f"variant {k!r} must be a mapping of addr/launcher/flag")
        v.update({kk: ("" if vv is None else str(vv)) for kk, vv in rv.items()})
        out[k] = v
    return out


def _groups_from_dict(raw: Any, valid_names: set, source: str) -> List[Group]:
    """Parse + validate the optional ``groups`` block → ordered ``[Group, …]``.

    Shape: a mapping of ``name -> [node, …]`` (insertion order preserved). The
    name is display-only (it labels a Select-line button), so it may be any
    non-empty string; member lists must reference real nodes — a typo there is a
    likely operator mistake, so it's rejected with a clear, Config-page-friendly
    message rather than silently dropped.
    """
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: 'groups' must be a mapping of name -> [node, ...]")
    groups: List[Group] = []
    seen = set()
    for name, members in raw.items():
        gname = str(name).strip()
        if not gname:
            raise ValueError(f"{source}: a group has an empty name")
        if gname in seen:
            raise ValueError(f"{source}: duplicate group name {gname!r}")
        seen.add(gname)
        if not isinstance(members, (list, tuple)):
            raise ValueError(
                f"{source}: group {gname!r} must be a list of node names")
        node_names = []
        for m in members:
            mn = str(m)
            if mn not in valid_names:
                raise ValueError(
                    f"{source}: group {gname!r} references unknown node {mn!r}")
            if mn not in node_names:                  # de-dup, keep first order
                node_names.append(mn)
        groups.append(Group(name=gname, nodes=tuple(node_names)))
    return groups


def load_fleet(filepath: str) -> Fleet:
    """Load and validate a fleet inventory from a YAML file."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return fleet_from_dict(raw, source=filepath)


def fleet_from_dict(raw: Dict[str, Any], source: str = "<dict>") -> Fleet:
    block = raw.get("fleet", raw)
    name = str(block.get("name", "fleet"))
    if not NAME_RE.match(name):
        raise ValueError(f"{source}: fleet name {name!r} must match {NAME_RE.pattern}")
    dd = block.get("defaults", {}) or {}
    base = FleetDefaults()
    defaults = replace(
        base,
        variant=dd.get("variant", base.variant),
        algo=dd.get("algo", base.algo),
        roleA_user=dd.get("roleA_user", base.roleA_user),
        roleB_user=dd.get("roleB_user", base.roleB_user),
        key_file=dd.get("key_file", base.key_file),
        deploy_root=dd.get("deploy_root", base.deploy_root),
        ssh_opts=dd.get("ssh_opts", base.ssh_opts),
        stagger=float(dd.get("stagger", base.stagger)),
        roleB_host_suffix=str(dd.get("roleB_host_suffix", base.roleB_host_suffix)),
        variants=_variants_from_dict(dd.get("variants")),
    )
    if defaults.variant not in VARIANTS:
        raise ValueError(f"{source}: invalid default variant {defaults.variant!r}")

    nodes = [_node_from_dict(n) for n in (block.get("nodes") or [])]
    if not nodes:
        raise ValueError(f"{source}: fleet has no nodes")

    # uniqueness invariants — duplicate id/name would break self-filtering
    ids = [n.id for n in nodes]
    names = [n.name for n in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{source}: duplicate node id(s) in fleet")
    if len(set(names)) != len(names):
        raise ValueError(f"{source}: duplicate node name(s) in fleet")

    groups = _groups_from_dict(block.get("groups"), set(names), source)

    return Fleet(name=name, defaults=defaults, nodes=nodes, groups=groups)
