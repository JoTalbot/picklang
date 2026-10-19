"""П.7b — MCP-интеграция: любой MCP-сервер становится библиотекой PICK.

Минимальный JSON-RPC (stdio) клиент Model Context Protocol:
initialize -> tools/list -> tools/call. Инструменты сервера регистрируются
как хост-функции вида "<server>.<tool>", и программа на PICK вызывает их
как обычные функции, ничего не зная про транспорт.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable


class McpError(Exception):
    pass


class McpClient:
    def __init__(self, command: list[str], name: str = "mcp"):
        self.name = name
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, encoding="utf-8")
        self._id = 0
        self.tools: list[dict[str, Any]] = []

    def _send(self, method: str, params: dict | None = None, notify: bool = False) -> Any:
        self._id += 1
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            msg["id"] = self._id
        if params:
            msg["params"] = params
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            raise McpError(f"MCP-сервер {self.name} закрылся, не ответив на {method}")
        resp = json.loads(line)
        if "error" in resp:
            raise McpError(f"{method}: {resp['error']}")
        return resp.get("result")

    def connect(self) -> list[dict[str, Any]]:
        self._send("initialize", {"protocolVersion": "2024-11-05",
                                  "capabilities": {}, "clientInfo": {"name": "picklang", "version": "0.1"}})
        self._send("notifications/initialized", notify=True)
        result = self._send("tools/list", {})
        self.tools = result.get("tools", []) if isinstance(result, dict) else []
        return self.tools

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        result = self._send("tools/call", {"name": tool, "arguments": arguments})
        if isinstance(result, dict) and "content" in result:
            texts = [c.get("text") for c in result["content"] if c.get("type") == "text"]
            if len(texts) == 1:
                try:
                    return json.loads(texts[0])
                except (TypeError, json.JSONDecodeError):
                    return texts[0]
            return texts
        return result

    def close(self) -> None:
        try:
            self.proc.terminate()
        except Exception:
            pass


def registry_from_mcp(client: McpClient) -> dict[str, Callable]:
    """tools/list -> реестр хост-функций PICK: имя = '<server>.<tool>'.

    Позиционные аргументы PICK отображаются на свойства inputSchema по порядку.
    """
    registry: dict[str, Callable] = {}
    for tool in client.tools:
        tool_name = tool["name"]
        props = list(((tool.get("inputSchema") or {}).get("properties") or {}).keys())

        def make(tool_name=tool_name, props=props):
            def call(*args):
                if props and len(args) == len(props):
                    arguments = dict(zip(props, args))
                elif len(args) == 1 and isinstance(args[0], dict):
                    arguments = args[0]
                else:
                    arguments = {"args": list(args)}
                return client.call_tool(tool_name, arguments)
            return call

        registry[f"{client.name}.{tool_name}"] = make()
    return registry
