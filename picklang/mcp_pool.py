"""П.6 — Пул MCP-соединений и батчинг вызовов.

Один живой процесс сервера на множество вызовов вместо спавна на каждый call;
batch — несколько tool calls одним заходом по открытому соединению.
"""
from __future__ import annotations

import sys
from typing import Any

from .mcp import McpClient


class McpPool:
    def __init__(self):
        self._clients: dict[tuple, McpClient] = {}
        self.stats = {"connects": 0, "calls": 0}

    def client(self, command: list[str], name: str) -> McpClient:
        key = tuple(command)
        if key not in self._clients:
            client = McpClient(command, name=name)
            client.connect()
            self._clients[key] = client
            self.stats["connects"] += 1
        return self._clients[key]

    def call(self, command: list[str], name: str, tool: str, arguments: dict) -> Any:
        self.stats["calls"] += 1
        return self.client(command, name).call_tool(tool, arguments)

    def batch(self, command: list[str], name: str, calls: list[tuple[str, dict]]) -> list[Any]:
        client = self.client(command, name)
        out = []
        for tool, arguments in calls:
            self.stats["calls"] += 1
            out.append(client.call_tool(tool, arguments))
        return out

    def close_all(self):
        for client in self._clients.values():
            client.close()
        self._clients.clear()
