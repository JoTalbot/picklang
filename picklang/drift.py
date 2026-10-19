"""П.6 — Дрейф контрактов инструментов: ловим изменение схемы на границе.

Инструмент поменял ответ (поле переименовали/убрали) — вместо падения
агента в проде получаем diff контракта и список программ, которых это ломает.
"""
from __future__ import annotations

from typing import Any


def compare_contracts(old: dict[str, dict], new: dict[str, dict]) -> dict[str, Any]:
    diff: dict[str, Any] = {"tools_added": [], "tools_removed": [], "fields": {}}
    for name in new:
        if name not in old:
            diff["tools_added"].append(name)
    for name in old:
        if name not in new:
            diff["tools_removed"].append(name)
            continue
        o, n = set(old[name].get("returns", [])), set(new[name].get("returns", []))
        if o != n:
            diff["fields"][name] = {"removed": sorted(o - n), "added": sorted(n - o)}
    diff["breaking"] = bool(diff["tools_removed"] or
                            any(v["removed"] for v in diff["fields"].values()))
    return diff


def affected_programs(diff: dict[str, Any], programs: dict[str, str]) -> list[dict[str, str]]:
    """Какие программы используют исчезнувшие поля/инструменты и что предложить."""
    out = []
    for prog_name, source in programs.items():
        for tool in diff.get("tools_removed", []):
            if tool in source:
                out.append({"program": prog_name, "problem": f"инструмент {tool} исчез",
                            "suggestion": "заменить вызов или удалить шаг"})
        for tool, fields in diff.get("fields", {}).items():
            for field in fields["removed"]:
                if f".{field}" in source:
                    added = fields["added"]
                    out.append({"program": prog_name,
                                "problem": f"{tool} больше не возвращает .{field}",
                                "suggestion": f"вероятно, переименовано в .{added[0]}" if added else "поле убрано совсем"})
    return out
