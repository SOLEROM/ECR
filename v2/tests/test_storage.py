"""Session storage: round-trip, listing, and path-traversal safety."""

from core.storage import SessionManager, SessionManifest, now_iso


def _mk(tmp_path):
    return SessionManager(str(tmp_path / "runs"))


def test_create_and_list(tmp_path):
    sm = _mk(tmp_path)
    sid = sm.generate_session_id("run")
    m = SessionManifest(session_id=sid, name="run", status="open",
                        created_at=now_iso(), variant="A", algo="default",
                        node_names=["d1", "d2"])
    sm.create_session(m, "fleet: {}")
    listed = sm.list_sessions()
    assert any(s["session_id"] == sid for s in listed)
    assert sm.get_session(sid) is not None


def test_traversal_rejected(tmp_path):
    sm = _mk(tmp_path)
    for evil in ["../../etc", "../secret", "/etc", "..\\..\\x"]:
        assert sm.get_session(evil) is None
        assert sm.delete_session(evil) is False


def test_zip_export_contains_core_files(tmp_path):
    sm = _mk(tmp_path)
    sid = sm.generate_session_id("x")
    m = SessionManifest(session_id=sid, name="x", status="open", created_at=now_iso())
    storage = sm.create_session(m, "fleet: {}")
    storage.append_log("d1-rx", "hello")
    import zipfile
    z = zipfile.ZipFile(storage.create_archive())
    names = "\n".join(z.namelist())
    assert "manifest.json" in names and "events.jsonl" in names
    assert "fleet_snapshot.yaml" in names
