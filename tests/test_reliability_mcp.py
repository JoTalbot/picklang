"""MCP-клиент: таймаут на молчащий сервер и нормальный roundtrip."""
from __future__ import annotations

import subprocess
import sys
import time

import pytest


def test_mcp_client_times_out_on_silent_server(tmp_path):
    from picklang.mcp import McpClient, McpError

    silent = tmp_path / "silent.py"
    silent.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    client = McpClient([sys.executable, str(silent)], name="silent", timeout=1.0)
    started = time.perf_counter()
    with pytest.raises(McpError):
        client.connect()
    client.close()
    assert time.perf_counter() - started < 10


def test_mcp_client_roundtrip_with_demo_server():
    from picklang.interpreter import run_program
    from picklang.mcp import McpClient, registry_from_mcp

    client = McpClient([sys.executable, "examples/mcp_server.py"], name="demo", timeout=15)
    try:
        tools = client.connect()
        registry = registry_from_mcp(client)
        assert {t["name"] for t in tools} >= {"add", "upper", "search"}
        out, _, _ = run_program("> t(a, b)\nr = demo.add(a, b)\nout r\n", registry, {"a": 2, "b": 3},
                                prefer_aot=False)
        assert out == 5
    finally:
        client.close()


def test_mcp_pool_closes_process_when_handshake_fails(tmp_path, monkeypatch):
    from picklang import mcp_pool
    from picklang.mcp import McpClient, McpError
    from picklang.mcp_pool import McpPool

    silent = tmp_path / "silent_pool_server.py"
    silent.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")

    clients = []

    class TrackingMcpClient(McpClient):
        def __init__(self, command, name="mcp"):
            super().__init__(command, name=name, timeout=0.2)
            clients.append(self)

    monkeypatch.setattr(mcp_pool, "McpClient", TrackingMcpClient)
    pool = McpPool()

    with pytest.raises(McpError):
        pool.client([sys.executable, str(silent)], name="silent")

    assert len(clients) == 1
    assert clients[0].proc.poll() is not None
    assert pool._clients == {}
    pool.close_all()

def test_mcp_client_kills_process_when_terminate_times_out():
    from picklang.mcp import McpClient

    events = []

    class StubbornProcess:
        killed = False

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout):
            events.append("wait")
            if not self.killed:
                raise subprocess.TimeoutExpired("mcp", timeout)

        def kill(self):
            events.append("kill")
            self.killed = True

    class Reader:
        def join(self, timeout):
            events.append("join")

    client = object.__new__(McpClient)
    client.proc = StubbornProcess()
    client._reader = Reader()
    client.close()

    assert events == ["terminate", "wait", "kill", "wait", "join"]

