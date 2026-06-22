"""Supervisor command synthesis + parsing (detached + pidfile, prefer-systemd)."""

from core import supervisor as sup


def test_detached_start_cmd():
    c = sup.detached_start_cmd("serviceA", "ID=1 ./variantA.run tcp")
    assert "setsid nohup" in c
    assert "/tmp/ccflet/serviceA.log" in c
    assert "/tmp/ccflet/serviceA.pid" in c
    assert "ID=1 ./variantA.run tcp" in c


def test_systemd_start_cmd():
    assert sup.systemd_start_cmd("serviceA") == \
        "systemctl start serviceA && echo ccflet-started source=systemd unit=serviceA"


def test_status_cmd_with_unit():
    c = sup.status_cmd("serviceA", "serviceA ", unit="serviceA")
    assert "/tmp/ccflet/serviceA.pid" in c
    assert "pgrep -f" in c
    assert "systemctl is-active" in c


def test_status_cmd_without_unit():
    c = sup.status_cmd("serviceB", "run_serviceB.py")
    assert "systemctl is-active" not in c
    assert "pgrep -f" in c


def test_stop_cmd():
    c = sup.stop_cmd("serviceB", "run_serviceB.py")
    assert "pkill -f" in c
    assert "/tmp/ccflet/serviceB.pid" in c


def test_parse_status_up():
    s = sup.parse_status_output("up pid=1234 source=pidfile")
    assert s == {"up": True, "pid": 1234, "source": "pidfile"}


def test_parse_status_down():
    assert sup.parse_status_output("down")["up"] is False


def test_parse_status_empty():
    assert sup.parse_status_output("")["up"] is False


def test_supervisor_prefers_systemd_when_installed(fake_ssh):
    ssh = fake_ssh(responses=[
        ("systemctl cat", ("", "", 0)),                 # unit installed
        ("systemctl start", ("ccflet-started source=systemd", "", 0)),
    ])
    r = sup.Supervisor(ssh).start("serviceA", "ID=1 ./variantA.run tcp",
                                  prefer_systemd="serviceA")
    assert r.success
    assert any("systemctl start serviceA" in c for c in ssh.commands)
    assert not any("setsid nohup" in c for c in ssh.commands)


def test_supervisor_falls_back_to_detached(fake_ssh):
    ssh = fake_ssh(responses=[
        ("systemctl cat", ("", "not found", 1)),        # unit NOT installed
        ("setsid nohup", ("ccflet-started pid=42 daemon=serviceA", "", 0)),
    ])
    r = sup.Supervisor(ssh).start("serviceA", "ID=1 ./variantA.run tcp",
                                  prefer_systemd="serviceA")
    assert r.success
    assert any("setsid nohup" in c for c in ssh.commands)


def test_supervisor_status_parsed(fake_ssh):
    ssh = fake_ssh(responses=[("/tmp/ccflet/", ("up pid=7 source=pidfile", "", 0))])
    st = sup.Supervisor(ssh).status("serviceB", "run_serviceB.py")
    assert st["up"] and st["pid"] == 7 and st["source"] == "pidfile"
