"""The Logs subsystem: the log-window model + the directory registry (pure — no
filesystem tailing, which lives in the I/O shell core/log_stream.py)."""

import os
import textwrap

import pytest

from core.logs import (
    logs_from_dict, LogsRegistry, LogWindow, DEFAULT_LINES, MAX_LINES,
)

GOOD = {"logs": {"default_lines": 150, "windows": [
    {"key": "syslog", "label": "System log", "process": "rsyslogd", "path": "/var/log/syslog"},
    {"key": "app", "process": "app.py", "path": "/tmp/app.log", "lines": 50},
]}}


# ---- model ------------------------------------------------------------------
def test_parse_good():
    wins = logs_from_dict(GOOD)
    assert [w.key for w in wins] == ["syslog", "app"]
    syslog, app = wins
    assert syslog.label == "System log" and syslog.process == "rsyslogd"
    assert syslog.path == "/var/log/syslog" and syslog.lines == 150   # file default_lines
    assert app.lines == 50                                            # per-window override


def test_label_defaults_to_process_then_key():
    wins = logs_from_dict({"logs": {"windows": [
        {"key": "a", "process": "procA", "path": "/x"},   # label → process
        {"key": "b", "path": "/y"},                       # label → key
    ]}})
    assert wins[0].label == "procA" and wins[1].label == "b"
    assert wins[1].lines == DEFAULT_LINES                 # no file/window override


def test_bare_block_accepted():
    wins = logs_from_dict({"windows": [{"key": "a", "path": "/x"}]})
    assert [w.key for w in wins] == ["a"]


def test_bad_key_rejected():
    with pytest.raises(ValueError, match="key"):
        logs_from_dict({"logs": {"windows": [{"key": "bad key", "path": "/x"}]}})


def test_missing_path_rejected():
    with pytest.raises(ValueError, match="path"):
        logs_from_dict({"logs": {"windows": [{"key": "a"}]}})


def test_empty_path_rejected():
    with pytest.raises(ValueError, match="path"):
        logs_from_dict({"logs": {"windows": [{"key": "a", "path": "  "}]}})


def test_non_integer_lines_rejected():
    with pytest.raises(ValueError, match="lines"):
        logs_from_dict({"logs": {"windows": [{"key": "a", "path": "/x", "lines": "lots"}]}})


def test_non_positive_lines_rejected():
    with pytest.raises(ValueError, match="lines"):
        logs_from_dict({"logs": {"windows": [{"key": "a", "path": "/x", "lines": 0}]}})


def test_lines_capped():
    wins = logs_from_dict({"logs": {"windows": [
        {"key": "a", "path": "/x", "lines": MAX_LINES * 10}]}})
    assert wins[0].lines == MAX_LINES


def test_duplicate_key_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        logs_from_dict({"logs": {"windows": [
            {"key": "a", "path": "/x"}, {"key": "a", "path": "/y"}]}})


def test_bad_default_lines_rejected():
    with pytest.raises(ValueError, match="default_lines"):
        logs_from_dict({"logs": {"default_lines": -3, "windows": []}})


def test_to_meta_shape():
    w = LogWindow(key="k", label="L", path="/p", process="proc", lines=10, hint="h")
    assert w.to_meta() == {"key": "k", "label": "L", "path": "/p",
                           "process": "proc", "lines": 10, "hint": "h"}


# ---- registry (loads a directory of log-window files) -----------------------
def _write(d, name, text):
    (d / name).write_text(textwrap.dedent(text))


def test_registry_loads_and_orders(tmp_path):
    _write(tmp_path, "a.yaml",
           "logs:\n  windows:\n    - {key: one, path: /v/1}\n    - {key: two, path: /v/2}\n")
    reg = LogsRegistry(str(tmp_path))
    assert [w.key for w in reg.windows] == ["one", "two"]
    assert reg.get("one").path == "/v/1" and reg.get("nope") is None


def test_registry_merges_files_in_name_order(tmp_path):
    _write(tmp_path, "a.yaml", "logs:\n  windows:\n    - {key: a, path: /x}\n")
    _write(tmp_path, "b.yaml", "logs:\n  windows:\n    - {key: b, path: /y}\n")
    reg = LogsRegistry(str(tmp_path))
    assert [w.key for w in reg.windows] == ["a", "b"]


def test_registry_skips_broken_file(tmp_path):
    _write(tmp_path, "good.yaml", "logs:\n  windows:\n    - {key: a, path: /x}\n")
    _write(tmp_path, "broken.yaml", "logs:\n  windows:\n    - {key: 'bad key', path: /y}\n")
    reg = LogsRegistry(str(tmp_path))
    assert [w.key for w in reg.windows] == ["a"]


def test_registry_dedupes_cross_file_keys(tmp_path):
    _write(tmp_path, "a.yaml", "logs:\n  windows:\n    - {key: dup, path: /x}\n")
    _write(tmp_path, "b.yaml", "logs:\n  windows:\n    - {key: dup, path: /y}\n")
    reg = LogsRegistry(str(tmp_path))
    assert [w.key for w in reg.windows] == ["dup"] and reg.get("dup").path == "/x"


def test_registry_reload_in_place(tmp_path):
    _write(tmp_path, "l.yaml", "logs:\n  windows:\n    - {key: a, path: /x}\n")
    reg = LogsRegistry(str(tmp_path))
    same = reg
    _write(tmp_path, "l.yaml", "logs:\n  windows:\n    - {key: b, path: /y}\n")
    reg.reload()
    assert reg is same and [w.key for w in reg.windows] == ["b"]


def test_registry_missing_dir_is_empty(tmp_path):
    reg = LogsRegistry(str(tmp_path / "nope"))
    assert reg.windows == []


def test_registry_loads_shipped_dir():
    # structural, not name-pinned: the shipped logs dir parses to ≥1 window with a path.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    reg = LogsRegistry(os.path.join(here, "yamls", "default", "logs"))
    assert reg.windows and all(w.key and w.path for w in reg.windows)
