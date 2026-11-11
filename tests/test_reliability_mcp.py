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

def test_mcp_client_serializes_concurrent_calls_and_matches_response_ids(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from picklang.mcp import McpClient

    server = tmp_path / "concurrent_mcp.py"
    server.write_text(
        """
import json
import sys
import threading
import time
from pathlib import Path

write_lock = threading.Lock()
workers = []
marker = sys.argv[1]

def respond(message, result):
    response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
    with write_lock:
        print(json.dumps(response), flush=True)

def handle(message):
    value = message["params"]["arguments"]["value"]
    if value == "slow":
        Path(marker).write_text("received", encoding="utf-8")
    time.sleep(0.15 if value == "slow" else 0.01)
    if value == "slow":
        with write_lock:
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                              "params": {"message": "working"}}), flush=True)
    respond(message, {"content": [{"type": "text", "text": json.dumps(value)}]})

for line in sys.stdin:
    message = json.loads(line)
    method = message["method"]
    if method == "initialize":
        respond(message, {"protocolVersion": "2024-11-05", "capabilities": {},
                          "serverInfo": {"name": "test", "version": "1"}})
    elif method == "tools/list":
        respond(message, {"tools": [{"name": "echo", "inputSchema": {
            "type": "object", "properties": {"value": {"type": "string"}}}}]})
    elif method == "tools/call":
        worker = threading.Thread(target=handle, args=(message,))
        worker.start()
        workers.append(worker)
    elif method == "notifications/initialized":
        pass

for worker in workers:
    worker.join()
""",
        encoding="utf-8",
    )
    client = McpClient([sys.executable, str(server), str(tmp_path / "slow-received")], name="parallel", timeout=5)
    try:
        client.connect()
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(client.call_tool, "echo", {"value": "slow"})
            marker = tmp_path / "slow-received"
            deadline = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            assert marker.exists(), "slow MCP request did not reach the server"
            fast = pool.submit(client.call_tool, "echo", {"value": "fast"})
            assert slow.result(timeout=5) == "slow"
            assert fast.result(timeout=5) == "fast"
    finally:
        client.close()



def test_mcp_client_uses_total_deadline_when_server_streams_notifications(tmp_path):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    from picklang.mcp import McpClient, McpError

    server = tmp_path / "notification_flood.py"
    server.write_text(
        """
import json
import sys
import time

def respond(message, result):
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)

for line in sys.stdin:
    message = json.loads(line)
    if message["method"] == "initialize":
        respond(message, {"protocolVersion": "2024-11-05", "capabilities": {},
                          "serverInfo": {"name": "test", "version": "1"}})
    elif message["method"] == "tools/list":
        respond(message, {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]})
    elif message["method"] == "tools/call":
        while True:
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                              "params": {"message": "still working"}}), flush=True)
            time.sleep(0.01)
""",
        encoding="utf-8",
    )
    client = McpClient([sys.executable, str(server)], name="notification-flood", timeout=0.15)
    try:
        client.connect()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.call_tool, "echo", {})
            with pytest.raises(McpError, match="не ответил"):
                try:
                    pending.result(timeout=2)
                except TimeoutError:
                    pytest.fail("MCP notifications reset the request deadline")
    finally:
        client.close()



def test_mcp_client_rejects_expired_deadline_even_with_queued_notification():
    import json
    import queue

    from picklang.mcp import McpClient, McpError

    client = object.__new__(McpClient)
    client.name = "expired"
    client.timeout = 1.0
    client._lines = queue.Queue()
    client._lines.put(json.dumps({
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {"message": "queued before read"},
    }))
    with pytest.raises(McpError, match="не ответил"):
        client._read_response("tools/call", time.monotonic() - 1.0)
