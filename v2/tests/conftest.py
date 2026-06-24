"""Shared test fixtures for ccflet. No network — everything is in-memory/mocked."""

import os
import sys
import time

import pytest

# make the ccflet package importable when running pytest from anywhere
CC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CC_ROOT not in sys.path:
    sys.path.insert(0, CC_ROOT)

from core.fleet import fleet_from_dict          # noqa: E402
from core.profiles import ProfileManager        # noqa: E402
from core.result import CommandResult           # noqa: E402


FLEET_DICT = {
    "fleet": {
        "name": "test-fleet",
        "defaults": {"variant": "A", "algo": "default", "stagger": 0},
        "nodes": [
            {"name": "d1", "id": 1, "host": "10.0.0.101", "subnet": "10.1.1"},
            {"name": "d2", "id": 2, "host": "10.0.0.102", "subnet": "10.1.2"},
            {"name": "d3", "id": 3, "host": "10.0.0.103", "subnet": "10.1.3"},
        ],
    }
}


@pytest.fixture
def fleet():
    return fleet_from_dict(FLEET_DICT)


@pytest.fixture
def profiles_dir():
    return os.path.join(CC_ROOT, "yamls", "default", "profiles")


@pytest.fixture
def profile_mgr(profiles_dir):
    return ProfileManager(profiles_dir)


class FakeSSH:
    """Records executed commands; returns canned results keyed by substring."""

    def __init__(self, responses=None, default=("", "", 0)):
        # responses: list of (substring, (stdout, stderr, code))
        self.responses = responses or []
        self.default = default
        self.commands = []
        self._connected = True

    @property
    def is_connected(self):
        return self._connected

    def connect(self):
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    def execute(self, command, timeout=None):
        self.commands.append(command)
        out, err, code = self.default
        for sub, resp in self.responses:
            if sub in command:
                out, err, code = resp
                break
        t = time.time()
        return CommandResult(command, code, out, err, t, t)


@pytest.fixture
def fake_ssh():
    return FakeSSH
