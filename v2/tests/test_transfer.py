"""Transfer command synthesis (hybrid rsync / scp jump-host)."""

from core import transfer as T


def test_rsync_push_cmd():
    cmd = T.rsync_push_cmd("payload/serviceB/", "/srv/ccfleet/roleA/serviceB/", "user",
                           "10.0.0.101", "-o BatchMode=yes")
    assert cmd[0] == "rsync"
    assert "-avzP" in cmd
    assert "-e" in cmd and "ssh -o BatchMode=yes" in cmd
    assert cmd[-1] == "user@10.0.0.101:/srv/ccfleet/roleA/serviceB/"


def test_scp_to_roleB_cmd_uses_jump_and_dash_O():
    cmd = T.scp_to_roleB_cmd("file", "/opt/file", "user", "10.0.0.101",
                             "root", "10.1.1.2", "-o ConnectTimeout=5")
    assert cmd[:2] == ["scp", "-O"]
    assert "-J" in cmd
    assert "user@10.0.0.101" in cmd
    assert cmd[-1] == "root@10.1.1.2:/opt/file"


def test_run_transfer_dry_run():
    cmd = T.rsync_push_cmd("a", "b", "u", "h")
    r = T.run_transfer(cmd, dry_run=True)
    assert r.success and r.stdout.startswith("[dry-run]")


def test_run_transfer_missing_binary():
    r = T.run_transfer(["definitely-not-a-binary-xyz", "arg"])
    assert not r.success
