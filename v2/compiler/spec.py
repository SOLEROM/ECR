"""
Spec I/O + the build.yaml status model.

The wish list lives in a ``system/`` folder. This module reads/writes its pieces and
the per-stage **status** (approved / draft / stale) that makes rebuilds safe (R7). It
holds *no* build logic — just loading, validation entry points, and the status book.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from . import STATUS_APPROVED, STATUS_DRAFT, STATUS_STALE

# files inside system/
DREAM = "layer1.dream.md"
PARAMS = "layer2.params.yaml"
SUBPARTS_DIR = "layer3.subparts"
BUILD = "build.yaml"
CATALOG = "catalog.md"


class SpecError(ValueError):
    """A wish-list file is missing or malformed."""


# --- build.yaml status book --------------------------------------------------
@dataclass
class StageState:
    artifact: str
    status: str = STATUS_DRAFT

    def to_dict(self) -> Dict[str, str]:
        return {"artifact": self.artifact, "status": self.status}


@dataclass
class BuildBook:
    """The parsed ``system/build.yaml`` — pipeline control + per-stage status."""
    app: str = "app"
    llm: str = "auto"               # auto | claude | offline
    stages: Dict[str, StageState] = field(default_factory=dict)
    path: Optional[str] = None

    def status_of(self, stage: str) -> str:
        st = self.stages.get(stage)
        return st.status if st else STATUS_DRAFT

    def set_status(self, stage: str, status: str):
        if stage in self.stages:
            self.stages[stage].status = status

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app": self.app,
            "llm": self.llm,
            "stages": {k: v.to_dict() for k, v in self.stages.items()},
        }


_DEFAULT_STAGES = {
    "params": StageState(PARAMS, STATUS_DRAFT),
    "subparts": StageState(SUBPARTS_DIR, STATUS_DRAFT),
    "app": StageState("..", STATUS_DRAFT),
}


def load_build(system_dir: str) -> BuildBook:
    """Load ``system/build.yaml`` (creating a default in memory if absent)."""
    path = os.path.join(system_dir, BUILD)
    raw: Dict[str, Any] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    stages: Dict[str, StageState] = {}
    raw_stages = raw.get("stages") or {}
    for name, default in _DEFAULT_STAGES.items():
        s = raw_stages.get(name) or {}
        stages[name] = StageState(
            artifact=s.get("artifact", default.artifact),
            status=s.get("status", default.status),
        )
    return BuildBook(app=raw.get("app", "app"), llm=raw.get("llm", "auto"),
                     stages=stages, path=path)


def save_build(book: BuildBook):
    if not book.path:
        raise SpecError("BuildBook has no path to save to")
    header = (
        "# system/build.yaml — Compiler pipeline control + per-stage status.\n"
        "# status: approved (you blessed it) · draft (Compiler-made) · stale (rebuild).\n"
        "# llm: auto | claude | offline  (which backend distill/expand use).\n"
    )
    with open(book.path, "w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(book.to_dict(), fh, sort_keys=False, default_flow_style=False)


# --- reading the layers ------------------------------------------------------
def read_dream(system_dir: str) -> str:
    path = os.path.join(system_dir, DREAM)
    if not os.path.exists(path):
        raise SpecError(f"missing {DREAM} — write the dream first (a paragraph is enough)")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def read_params(system_dir: str) -> Dict[str, Any]:
    path = os.path.join(system_dir, PARAMS)
    if not os.path.exists(path):
        raise SpecError(f"missing {PARAMS} — run `compile.sh --only params` first")
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    app = raw.get("app") or raw
    if not app.get("name"):
        raise SpecError(f"{PARAMS}: app.name is required")
    return raw


def write_params(system_dir: str, params: Dict[str, Any]):
    path = os.path.join(system_dir, PARAMS)
    header = ("# layer2.params.yaml — the few global facts true for the whole app.\n"
              "# Drafted by `distill` from the dream; EDIT THIS, then mark it approved.\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(params, fh, sort_keys=False, default_flow_style=False)


def subparts_dir(system_dir: str) -> str:
    return os.path.join(system_dir, SUBPARTS_DIR)


def read_subparts(system_dir: str) -> Dict[str, Dict[str, Any]]:
    """All ``layer3.subparts/*.yaml`` keyed by file stem (skip a part → template default)."""
    d = subparts_dir(system_dir)
    out: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if fn.endswith((".yaml", ".yml")):
            with open(os.path.join(d, fn), "r", encoding="utf-8") as fh:
                out[os.path.splitext(fn)[0]] = yaml.safe_load(fh) or {}
    return out


def write_subpart(system_dir: str, name: str, data: Dict[str, Any], header: str = ""):
    d = subparts_dir(system_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        if header:
            fh.write(header.rstrip() + "\n")
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
    return path
