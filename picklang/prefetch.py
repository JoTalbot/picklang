"""П.5 — Префетч на ожидании человека.

Пока исполнение висит на @wait, читающие шаги после него можно выполнить
заранее и положить в кэш: человек нажал «да» — половина работы уже готова.
Пишущие/опасные шаги не префетчатся никогда.
"""
from __future__ import annotations

from typing import Any, Callable

from .parser import parse

READ_SUFFIXES = ("get", "read", "search", "list", "fetch", "query", "plan", "top", "fields")


def _calls(expr):
    if isinstance(expr, tuple):
        if expr[0] == "call":
            yield expr[1]
        for x in expr[1:]:
            if isinstance(x, tuple):
                yield from _calls(x)
            elif isinstance(x, list):
                for y in x:
                    if isinstance(y, tuple):
                        yield from _calls(y)


def prefetch_after_wait(source: str, registry: dict[str, Callable], env: dict[str, Any]) -> dict[str, Any]:
    """Выполнить read-only присваивания после первого @wait. Возвращает добавки в env."""
    from .interpreter import _State, _eval
    steps = parse(source)
    wait_idx = next((i for i, s in enumerate(steps) if s.kind == "wait"), None)
    if wait_idx is None:
        return {}
    out = dict(env)
    state = _State()
    for step in steps[wait_idx + 1:]:
        if step.kind != "assign":
            continue
        names = list(_calls(step.expr))
        if not names or not all(n.split(".")[-1] in READ_SUFFIXES or n in ("top", "fields") for n in names):
            continue
        try:
            out[step.target] = _eval(step.expr, out, registry, None, [], state)
        except Exception:
            pass  # префетч — оптимизация, а не обязательство
    return {k: v for k, v in out.items() if k not in env}
