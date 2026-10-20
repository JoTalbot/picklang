"""MCP-клиент: таймаут на молчащий сервер и нормальный roundtrip."""
from __future__ import annotations

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
