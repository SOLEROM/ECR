"""
Fork the template into a standalone app dir (D1 — standalone fork per app).

A fork is a full copy of the template's generic engine + its ``system/`` wish list, so
the Compiler edits the copy and the engine stays ≈ the template (upstream-pullable).
Build outputs, caches, the venv and other forks are never copied.
"""

import os
import shutil
from typing import List

# never copied into a fork
_EXCLUDE = {".venv", ".git", "runs", "apps", "__pycache__", ".pytest_cache",
            "node_modules"}


def _ignore(_dir: str, names: List[str]):
    return [n for n in names if n in _EXCLUDE or n.endswith(".pyc")]


def fork(template_dir: str, dest_dir: str, force: bool = False) -> str:
    """Copy ``template_dir`` → ``dest_dir`` (a new app). Returns ``dest_dir``."""
    if os.path.exists(dest_dir):
        if not force:
            raise FileExistsError(
                f"{dest_dir} already exists (use --force to overwrite)")
        shutil.rmtree(dest_dir)
    os.makedirs(os.path.dirname(os.path.abspath(dest_dir)), exist_ok=True)
    shutil.copytree(template_dir, dest_dir, ignore=_ignore, symlinks=True)
    # a fork keeps no inherited manifest from the template
    stale = os.path.join(dest_dir, ".compiler-manifest.json")
    if os.path.exists(stale):
        os.remove(stale)
    return dest_dir
