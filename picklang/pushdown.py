"""П.4 — Проекции внутрь инструментов (pushdown).

Вместо «получить всё -> отрезать top/fields у себя» вызов сразу несёт
limit/fields аргументами: сервер инструмента применяет их на своей стороне,
и полный результат вообще не гоняется по сети/транспорту.
Для MCP это ложится на inputSchema инструмента: search(query, limit, fields).
"""
from __future__ import annotations

from typing import Any, Callable


def pushdown_search(fn: Callable, name: str = "search") -> Callable:
    """Обернуть сырой поиск fn(query)->[все результаты] в серверно-режущий вариант.

    Возвращаемая функция принимает (query, limit=None, fields=None) и применяет
    проекции ДО возврата: вызывающий слой физически не получает лишнего.
    """
    def call(query, limit=None, fields=None):
        results = fn(query)
        transferred = results  # то, что «уезжает по сети» после серверной резки
        if limit is not None:
            transferred = transferred[: int(limit)]
        if fields:
            names = [f.strip() for f in str(fields).split(",")]
            transferred = [{k: r.get(k) for k in names} if isinstance(r, dict) else r
                           for r in transferred]
        return transferred
    call.__name__ = name
    return call


def transferred_tokens(results: Any) -> int:
    from .result_guard import estimate_tokens
    return estimate_tokens(results)
