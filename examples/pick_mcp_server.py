#!/usr/bin/env python3
"""П.18 — Сам PICK как MCP-инструменты: pick_check, pick_digest, pick_run.

Любой агент с MCP-клиентом получает наш язык и контроль как инструменты,
не зная про этот репозиторий ничего, кроме адреса сервера.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from picklang import run_program          # noqa: E402
from picklang.check import check_source   # noqa: E402
from picklang.ir import program_digest    # noqa: E402
from picklang.stdlib import with_stdlib   # noqa: E402

TOOLS = [
    {"name": "pick_check", "description": "Статическая проверка программы PICK",
     "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}}}},
    {"name": "pick_digest", "description": "Digest программы PICK",
     "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}}}},
    {"name": "pick_run", "description": "Выполнить программу PICK со стандартной библиотекой",
     "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "inputs": {"type": "object"}}}},
]


def handle(msg):
    method = msg.get("method")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "pick", "version": "0.2"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments", {})
        if name == "pick_check":
            value = check_source(args["source"])
        elif name == "pick_digest":
            value = program_digest(args["source"])
        elif name == "pick_run":
            value, _env, _trace = run_program(args["source"], with_stdlib({}), args.get("inputs") or {})
        else:
            value = {"error": f"unknown tool {name}"}
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
    return {}


for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    if "id" in msg:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}), flush=True)
