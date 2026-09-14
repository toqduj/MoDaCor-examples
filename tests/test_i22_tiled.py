from __future__ import annotations

from types import SimpleNamespace

import pytest

from i22_tiled import LocalI22TiledServer


class FakeThread:
    def __init__(self, *, stops_on_join: bool = True):
        self.alive = True
        self.stops_on_join = stops_on_join
        self.join_timeout = None

    def is_alive(self):
        return self.alive

    def join(self, timeout):
        self.join_timeout = timeout
        if self.stops_on_join:
            self.alive = False


def test_tiled_server_validates_configuration(tmp_path):
    with pytest.raises(ValueError, match="source_paths"):
        LocalI22TiledServer([])
    with pytest.raises(ValueError, match="port"):
        LocalI22TiledServer([tmp_path / "sample.nxs"], port=0)
    with pytest.raises(ValueError, match="timeouts"):
        LocalI22TiledServer([tmp_path / "sample.nxs"], startup_timeout=0)


def test_running_server_start_is_idempotent_without_rebuilding(tmp_path):
    tiled = LocalI22TiledServer([tmp_path / "sample.nxs"])
    tiled.server = SimpleNamespace(should_exit=False, started=True)
    tiled.thread = FakeThread()

    assert tiled.start() == tiled.url
    assert tiled.running is True


def test_stop_signals_and_joins_owned_thread(tmp_path):
    tiled = LocalI22TiledServer([tmp_path / "sample.nxs"], shutdown_timeout=2)
    server = SimpleNamespace(should_exit=False, started=True)
    thread = FakeThread()
    tiled.server = server
    tiled.thread = thread

    tiled.stop()

    assert server.should_exit is True
    assert thread.join_timeout == 2.0
    assert tiled.running is False
    assert tiled.server is None
    assert tiled.thread is None


def test_stop_retains_state_when_thread_does_not_exit(tmp_path):
    tiled = LocalI22TiledServer([tmp_path / "sample.nxs"], shutdown_timeout=2)
    tiled.server = SimpleNamespace(should_exit=False, started=True)
    tiled.thread = FakeThread(stops_on_join=False)

    with pytest.raises(TimeoutError, match="did not stop"):
        tiled.stop()

    assert tiled.server is not None
    assert tiled.thread is not None
