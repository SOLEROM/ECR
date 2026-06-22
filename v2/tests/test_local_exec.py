"""Local (base-station) command runner (D8): real run, echo, error paths."""

from core.local_exec import run_local


def test_run_local_echo():
    r = run_local("echo hello-local")
    assert r.success and "hello-local" in r.stdout


def test_run_local_dry_run_does_not_execute():
    r = run_local("echo nope", dry_run=True)
    assert r.success and r.stdout.startswith("[dry-run] (local) echo nope")


def test_run_local_env_is_passed():
    r = run_local("echo $CCFLET_X", env={"CCFLET_X": "42"})
    assert "42" in r.stdout


def test_run_local_argv_list_and_nonzero_exit():
    r = run_local(["bash", "-c", "exit 3"])
    assert not r.success and r.exit_code == 3


def test_run_local_missing_binary():
    r = run_local(["/nonexistent/ccflet-binary"])
    assert not r.success and "not found" in r.stderr


def test_run_local_timeout():
    r = run_local("sleep 5", timeout=0.3)
    assert not r.success and "timed out" in r.stderr
