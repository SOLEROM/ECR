"""
Design-docs browser backend for the in-app Help view.

The Help page renders the markdown under ``design/`` as a navigable tree. The tree
is read from disk on **every** request, so adding / renaming / removing a file in
``design/`` is reflected immediately — nothing about the pages is hardcoded
(the front asked for an adjustable, not pre-fixed, tree).

Safety: docs are resolved **path-safely** under the design root (no traversal,
``.md`` only) — the same boundary discipline as ``storage._safe_dir``. Markdown is
rendered to HTML server-side with python-markdown (tables + fenced code). The
design files are team-authored and trusted (same trust level as the templates), so
their rendered HTML is injected with innerHTML on the client; untrusted strings
elsewhere (operator notes, usernames) still use textContent.

Pure-ish: every function is a function of (root, relpath) + the files on disk, so
it is unit-tested against a tmp dir with no network (see tests/test_docs.py).
"""

import os
import re

import markdown as _markdown
import yaml

MD_EXT = ".md"
# `extra` bundles tables + fenced_code (+ attr_list, def_list, …); `sane_lists`
# fixes mixed ordered/unordered nesting; `toc` gives heading ids for #anchors.
_MD_EXTENSIONS = ["extra", "sane_lists", "toc"]
_DEFAULT_ORDER = 999
_FENCE = "---"
_PREFIX_RE = re.compile(r"^\d+[-_]")


def split_frontmatter(text):
    """Split a leading ``---`` YAML block off ``text`` → (meta:dict, body:str).

    Returns ({}, text) when there is no well-formed frontmatter. Never raises on
    bad YAML — a malformed block yields empty meta and the body unchanged.
    """
    if not text.startswith(_FENCE):
        return {}, text
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if end is None:
        return {}, text
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    body = "\n".join(lines[end + 1:])
    return meta, body


def render_markdown(body):
    """Render markdown body → HTML (tables, fenced code, heading anchors)."""
    return _markdown.markdown(body, extensions=_MD_EXTENSIONS, output_format="html5")


def title_from_name(name):
    """Derive a readable title from a filename: ``01-action-profiles.md`` → ``Action profiles``."""
    base = name[:-len(MD_EXT)] if name.lower().endswith(MD_EXT) else name
    base = _PREFIX_RE.sub("", base).replace("-", " ").replace("_", " ").strip()
    return (base[:1].upper() + base[1:]) if base else name


def safe_resolve(root, relpath):
    """Resolve ``relpath`` under ``root`` or return None.

    Rejects traversal (``..``), absolute paths, anything escaping the root, and
    non-``.md`` / missing files. Mirrors the path-safety rule used for sessions.
    """
    if not root:
        return None
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, relpath or ""))
    if target != root_real and not target.startswith(root_real + os.sep):
        return None
    if not target.lower().endswith(MD_EXT) or not os.path.isfile(target):
        return None
    return target


def _file_meta(abs_path):
    try:
        with open(abs_path, encoding="utf-8") as fh:
            meta, _ = split_frontmatter(fh.read())
        return meta
    except OSError:
        return {}


def _order_of(meta):
    try:
        return int(meta.get("order"))
    except (TypeError, ValueError):
        return _DEFAULT_ORDER


def _sort_key(node):
    if node["type"] == "dir":
        orders = [c["order"] for c in node["children"] if c["type"] == "file"]
        return (min(orders) if orders else _DEFAULT_ORDER, node["name"].lower())
    return (node["order"], node["name"].lower())


def build_tree(root):
    """Walk ``root`` → a nested, sorted tree of design docs.

    Node shapes::

        file: {type:"file", name, path(rel), title, order, summary}
        dir : {type:"dir",  name, path(rel), children:[...]}

    Dirs sort by their lowest child ``order`` then name; files by ``order`` then
    name. Dotfiles are skipped; empty dirs are pruned.
    """
    if not root or not os.path.isdir(root):
        return []
    root_real = os.path.realpath(root)

    def walk(abs_dir, rel_dir):
        out = []
        try:
            names = os.listdir(abs_dir)
        except OSError:
            return out
        for name in names:
            if name.startswith("."):
                continue
            abs_p = os.path.join(abs_dir, name)
            rel_p = os.path.join(rel_dir, name) if rel_dir else name
            if os.path.isdir(abs_p):
                children = walk(abs_p, rel_p)
                if children:
                    out.append({"type": "dir", "name": name, "path": rel_p,
                                "children": children})
            elif name.lower().endswith(MD_EXT):
                meta = _file_meta(abs_p)
                out.append({
                    "type": "file", "name": name, "path": rel_p,
                    "title": str(meta.get("title") or title_from_name(name)),
                    "order": _order_of(meta),
                    "summary": str(meta.get("summary") or ""),
                })
        out.sort(key=_sort_key)
        return out

    return walk(root_real, "")


def first_doc(tree):
    """Return the path of the first file in a tree (depth-first), or None."""
    for node in tree:
        if node["type"] == "file":
            return node["path"]
        found = first_doc(node["children"])
        if found:
            return found
    return None


def read_doc(root, relpath):
    """Read + render one design doc, or None if it can't be resolved safely."""
    target = safe_resolve(root, relpath)
    if not target:
        return None
    try:
        with open(target, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    meta, body = split_frontmatter(text)
    rel = os.path.relpath(target, os.path.realpath(root))
    return {
        "path": rel,
        "title": str(meta.get("title") or title_from_name(os.path.basename(target))),
        "summary": str(meta.get("summary") or ""),
        "html": render_markdown(body),
        "mtime": os.path.getmtime(target),
    }
