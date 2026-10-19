"""П.10 — Компактная сериализация табличных результатов для контекста.

Список словарей в JSON повторяет ключи в каждой строке. TSV-укладка тех же
данных (заголовок один раз) заметно дешевле на списках.
"""
from __future__ import annotations

from typing import Any


def to_compact(value: Any) -> str:
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        keys = list(value[0].keys())
        lines = ["\t".join(keys)]
        for row in value:
            lines.append("\t".join("" if row.get(k) is None else str(row.get(k)) for k in keys))
        return "\n".join(lines)
    if isinstance(value, dict):
        return "\n".join(f"{k}\t{v}" for k, v in value.items())
    return str(value)


def compact_tokens(value: Any) -> tuple[int, int]:
    import json

    from .result_guard import estimate_tokens
    return estimate_tokens(json.dumps(value, ensure_ascii=False)), estimate_tokens(to_compact(value))
