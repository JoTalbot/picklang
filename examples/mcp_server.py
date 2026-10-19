#!/usr/bin/env python3
"""Минимальный MCP-сервер (stdio, JSON-RPC) с двумя инструментами для демо/тестов."""
import json
import sys

TOOLS = [
    {"name": "add", "description": "Сложить два числа",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}},
    {"name": "upper", "description": "Верхний регистр",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "search", "description": "Поиск с серверной проекцией",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"},
                     "limit": {"type": "number"}, "fields": {"type": "string"}}}},
]

RESULTS = [{"title": f"r{i}", "url": f"u{i}", "body": "x" * 300} for i in range(10)]


def handle(msg):
    method = msg.get("method")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "pick-demo", "version": "0.1"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments", {})
        if name == "search":
            value = RESULTS[: int(args.get("limit", 10))]
            if args.get("fields"):
                names = [f.strip() for f in args["fields"].split(",")]
                value = [{k: r.get(k) for k in names} for r in value]
        else:
            value = (args["a"] + args["b"]) if name == "add" else str(args["text"]).upper()
        return {"content": [{"type": "text", "text": json.dumps(value)}]}
    return {}


for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    if "id" in msg:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}), flush=True)
