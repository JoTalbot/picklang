"""Стандартная библиотека проекций: режет результаты инструментов ДО контекста модели.

Главный расход агента — не код, а сырые ответы инструментов. Проекции выполняются
рантаймом: в дальнейшую программу (и в контекст LLM) попадает выжимка, полное
значение при необходимости адресуется по ссылке из CAS (см. ToolResultGuard).
"""
from __future__ import annotations

from typing import Any


def top(items: list, n: int = 3) -> list:
    return list(items)[: int(n)]


def fields(item: Any, *names: str) -> Any:
    """Оставить только нужные поля: dict -> dict, list[dict] -> list[dict]."""
    if isinstance(item, list):
        return [fields(x, *names) for x in item]
    if isinstance(item, dict):
        return {k: item.get(k) for k in names}
    return item


def take(text: Any, chars: int = 500) -> str:
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= chars else s[: int(chars)] + "…"


def keys(item: dict) -> list:
    return sorted(item.keys()) if isinstance(item, dict) else []


def count(items: Any) -> int:
    return len(items)


STDLIB = {"top": top, "fields": fields, "take": take, "keys": keys, "count": count}


def with_stdlib(registry: dict) -> dict:
    merged = dict(STDLIB)
    merged.update(registry)
    return merged
