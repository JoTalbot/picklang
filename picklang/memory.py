"""П.3 — Консолидация памяти: эпизоды сжимаются в уроки.

Вместо всех логов агент получает выводы вида «bank.charge: 3/10 падений
по пятницам» — сгруппированные по инструменту повторяющиеся исходы.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def consolidate(episodes: list[dict[str, Any]], min_repeat: int = 2) -> list[dict[str, Any]]:
    """episodes: [{tool, ok, error?, latency_ms?}] -> уроки."""
    by_tool: dict[str, list] = defaultdict(list)
    for ep in episodes:
        by_tool[ep["tool"]].append(ep)
    lessons = []
    for tool, eps in sorted(by_tool.items()):
        fails = [e for e in eps if not e.get("ok", True)]
        errors = Counter(e.get("error", "?") for e in fails)
        lat = [e["latency_ms"] for e in eps if "latency_ms" in e]
        lesson: dict[str, Any] = {"tool": tool, "calls": len(eps), "fails": len(fails)}
        if fails and len(fails) >= min_repeat:
            lesson["lesson"] = (f"{tool}: падает {len(fails)}/{len(eps)}, "
                                f"доминирующая ошибка: {errors.most_common(1)[0][0]}")
        elif lat:
            lesson["lesson"] = f"{tool}: стабилен, медианная задержка {sorted(lat)[len(lat) // 2]} мс"
        else:
            lesson["lesson"] = f"{tool}: {len(eps)} вызовов без особенностей"
        lessons.append(lesson)
    return lessons
