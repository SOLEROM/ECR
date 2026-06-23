"""
Fork the template into a standalone app dir (D1 — standalone fork per app).

A fork is a full copy of the template's generic engine + its ``system/`` wish list, so
the Compiler edits the copy and the engine stays ≈ the template (upstream-pullable).
Build outputs, caches, the venv and other forks are never copied.
"""

import os
import shutil
from typing import List

from . import spec

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
    _reset_status(dest_dir)
    return dest_dir


def _reset_status(dest_dir: str):
    """Un-bless the inherited stage statuses so the fork's *first* build runs.

    The template's ``build.yaml`` marks the demo's ``params``/``subparts`` ``approved``
    (they're hand-authored and locked against an accidental redistill). Those blessings
    belong to the demo, not to a brand-new app: if they carried into the fork, the
    approved-lock would halt the very first ``--from dream --to app`` and silently emit
    nothing. Reset every stage to ``draft`` so the documented first build flows through
    distill → expand → build → gate. (``llm:`` is left as-is.)
    """
    system_dir = os.path.join(dest_dir, "system")
    book = spec.load_build(system_dir)
    for stage in book.stages:
        book.set_status(stage, spec.STATUS_DRAFT)
    if book.path:
        spec.save_build(book)
