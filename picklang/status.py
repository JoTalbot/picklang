"""П.4 — Компактный протокол статуса: одна строка на шаг вместо логов.

Агенту для следующего решения нужны: что сделано, короткий digest результата
и что осталось. Всё остальное — шум за его же токены.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _short_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:8]


def status_line(seq: int, name: str, result: Any = None, ok: bool = True) -> str:
    mark = "ok" if ok else "FAIL"
    return f"{mark} {seq} {name} d={_short_digest(result)}"


def status_protocol(trace: list[str], results: list[Any] | None = None,
                    remaining: list[str] | None = None) -> str:
    results = results or [None] * len(trace)
    lines = [status_line(i + 1, name, res) for i, (name, res) in enumerate(zip(trace, results))]
    if remaining:
        lines.append("осталось: " + ", ".join(remaining))
    return "\n".join(lines)


def status_from_events(events: list[dict]) -> str:
    """Тот же протокол, восстановленный из журнала durable-исполнителя."""
    lines, seq = [], 0
    for ev in events:
        if ev["type"] == "ActivityCompleted":
            seq += 1
            lines.append(f"ok {seq} {ev['data']['activity']} d={ev['data']['result_ref'][:8]}")
        elif ev["type"] == "WorkflowCompleted":
            lines.append(f"done d={ev['hash'][:8]}")
        elif ev["type"] == "WorkflowWaiting":
            lines.append(f"wait {ev['data']['signal']}")
        elif ev["type"] == "ProcessCrashed":
            lines.append("crash (возобновляемо)")
    return "\n".join(lines)
