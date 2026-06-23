"""
``.compiler-manifest.json`` — the record of every Compiler-owned path.

Two jobs:
  - **Safe clobber**: a rebuild overwrites exactly the owned paths, nothing else.
  - **`--check`** (D3 smell test): warn if a human hand-edited a generated file
    (the spec is the source of truth — edit ``system/`` and rebuild, don't patch output).

Each entry records the path + a content hash at emit time; ``--check`` re-hashes and
reports drift.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List

MANIFEST_NAME = ".compiler-manifest.json"


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Manifest:
    app_dir: str
    owned: Dict[str, str] = field(default_factory=dict)   # relpath -> sha256
    meta: Dict[str, str] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return os.path.join(self.app_dir, MANIFEST_NAME)

    def record(self, relpath: str):
        """Hash an emitted file and record ownership."""
        full = os.path.join(self.app_dir, relpath)
        if os.path.exists(full):
            self.owned[relpath] = _sha(full)

    def save(self, meta: Dict[str, str] = None):
        self.meta.update(meta or {})
        payload = {"meta": self.meta, "owned": self.owned}
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")

    @classmethod
    def load(cls, app_dir: str) -> "Manifest":
        path = os.path.join(app_dir, MANIFEST_NAME)
        if not os.path.exists(path):
            return cls(app_dir=app_dir)
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh) or {}
        return cls(app_dir=app_dir, owned=payload.get("owned", {}),
                   meta=payload.get("meta", {}))

    def check(self) -> List[str]:
        """Return owned paths whose on-disk content drifted from the recorded hash
        (a human likely hand-edited a generated file — a smell under D3)."""
        drifted = []
        for relpath, recorded in sorted(self.owned.items()):
            full = os.path.join(self.app_dir, relpath)
            if not os.path.exists(full):
                drifted.append(f"{relpath} (missing)")
            elif _sha(full) != recorded:
                drifted.append(f"{relpath} (edited)")
        return drifted
