"""Design-docs browser: dynamic tree, frontmatter, path-safety, rendering."""

from core import docs


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


FM = """---
title: Architecture
order: 1
summary: The shape of the system.
---

# Architecture

Some **body** text.

| a | b |
|---|---|
| 1 | 2 |
"""


def test_split_frontmatter():
    meta, body = docs.split_frontmatter(FM)
    assert meta["title"] == "Architecture" and meta["order"] == 1
    assert body.lstrip().startswith("# Architecture")


def test_split_frontmatter_absent():
    meta, body = docs.split_frontmatter("# no fm\nhi")
    assert meta == {} and body == "# no fm\nhi"


def test_split_frontmatter_bad_yaml_is_safe():
    meta, body = docs.split_frontmatter("---\n: : : bad\n---\nbody")
    assert isinstance(meta, dict) and "body" in body


def test_render_markdown_tables_and_code():
    html = docs.render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nx\n```")
    assert "<table>" in html and "<code>" in html


def test_title_from_name():
    assert docs.title_from_name("01-action-profiles.md") == "Action profiles"
    assert docs.title_from_name("README.md") == "README"


def test_build_tree_sorted_and_dynamic(tmp_path):
    _write(tmp_path, "02-scope.md", "---\ntitle: Scope\norder: 2\n---\nx")
    _write(tmp_path, "01-arch.md", "---\ntitle: Arch\norder: 1\n---\nx")
    _write(tmp_path, "sub/03-deep.md", "---\ntitle: Deep\norder: 3\n---\nx")
    _write(tmp_path, "notes.txt", "ignored")
    _write(tmp_path, ".hidden.md", "skip")
    tree = docs.build_tree(str(tmp_path))
    files = [n for n in tree if n["type"] == "file"]
    assert [f["title"] for f in files] == ["Arch", "Scope"]   # order-sorted
    assert any(n["type"] == "dir" and n["name"] == "sub" for n in tree)
    # adding a file shows up on the next walk (dynamic, not pre-fixed)
    _write(tmp_path, "00-readme.md", "---\ntitle: Readme\norder: 0\n---\nx")
    again = docs.build_tree(str(tmp_path))
    assert [n["title"] for n in again if n["type"] == "file"][0] == "Readme"


def test_first_doc(tmp_path):
    _write(tmp_path, "01-a.md", "---\norder: 1\n---\na")
    _write(tmp_path, "00-b.md", "---\norder: 0\n---\nb")
    assert docs.first_doc(docs.build_tree(str(tmp_path))) == "00-b.md"


def test_read_doc_ok(tmp_path):
    _write(tmp_path, "01-arch.md", FM)
    doc = docs.read_doc(str(tmp_path), "01-arch.md")
    assert doc["title"] == "Architecture"
    assert "<table>" in doc["html"] and "<h1" in doc["html"]


def test_read_doc_traversal_rejected(tmp_path):
    _write(tmp_path, "01-arch.md", FM)
    secret = tmp_path.parent / "secret.md"
    secret.write_text("top secret", encoding="utf-8")
    for evil in ["../secret.md", "../../etc/passwd", "/etc/passwd",
                 "..\\secret.md", "nope.md", "01-arch.txt"]:
        assert docs.read_doc(str(tmp_path), evil) is None
        assert docs.safe_resolve(str(tmp_path), evil) is None
