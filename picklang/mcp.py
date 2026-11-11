"""П.7b — MCP-интеграция: любой MCP-сервер становится библиотекой PICK.

Минимальный JSON-RPC (stdio) клиент Model Context Protocol:
initialize -> tools/list -> tools/call. Инструменты сервера регистрируются
как хост-функции вида "<server>.<tool>", и программа на PICK вызывает их
как обычные функции, ничего не зная про транспорт.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Any, Callable


class McpError(Exception):
    pass


class McpClient:
    def __init__(self, command: list[str], name: str = "mcp", timeout: float | None = 30.0):
        self.name = name
        self.timeout = timeout
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, encoding="utf-8")
        self._id = 0
        self._request_lock = threading.Lock()
        self.tools: list[dict[str, Any]] = []
        # Читаем stdout в отдельном потоке: readline() без таймаута вешает клиента
        # навсегда, если сервер принял соединение и молчит.
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)  # EOF: сервер закрылся

    def _read_response(self, method: str, deadline: float | None) -> str:
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            line = self._lines.get() if remaining is None else self._lines.get(timeout=remaining)
        except queue.Empty as exc:
            limit = "без таймаута" if self.timeout is None else f"за {self.timeout:g} с"
            raise McpError(
                f"MCP-сервер {self.name} не ответил на {method} {limit}"
            ) from exc
        if line is None:
            raise McpError(f"MCP-сервер {self.name} закрылся, не ответив на {method}")
        return line

    def _send(self, method: str, params: dict | None = None, notify: bool = False) -> Any:
        # stdio is a single request/response stream. Serialize the whole exchange so
        # parallel PICK branches cannot consume each other's responses.
        with self._request_lock:
            msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            request_id = None
            if not notify:
                self._id += 1
                request_id = self._id
                msg["id"] = request_id
            if params:
                msg["params"] = params
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            if notify:
                return None

            deadline = None if self.timeout is None else time.monotonic() + max(0.0, self.timeout)
            while True:
                try:
                    resp = json.loads(self._read_response(method, deadline))
                except json.JSONDecodeError as exc:
                    raise McpError(f"{method}: MCP-сервер вернул некорректный JSON") from exc
                if not isinstance(resp, dict):
                    raise McpError(f"{method}: MCP-сервер вернул не объект JSON-RPC")
                # Notifications may be interleaved with a request response and have
                # no id. They are not the answer to the outstanding request.
                if "id" not in resp:
                    continue
                if resp.get("jsonrpc") != "2.0" or resp.get("id") != request_id:
                    raise McpError(
                        f"{method}: неверная идентичность JSON-RPC ответа "
                        f"(ожидался id={request_id}, получен id={resp.get('id')!r})"
                    )
                if "error" in resp:
                    raise McpError(f"{method}: {resp['error']}")
                if "result" not in resp:
                    raise McpError(f"{method}: JSON-RPC ответ не содержит result")
                return resp["result"]

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
            # The process may already have exited; still wait/reap it below.
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # A server may ignore SIGTERM. Escalate so a failed MCP handshake
            # cannot leave a child process running indefinitely.
            try:
                self.proc.kill()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
        except Exception:
            pass
        self._reader.join(timeout=2)


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
