"""П.8 — Замкнутый контур самовосстановления.

генерация -> автопатчи -> проверка -> (если осталось) компактный запрос
модели только на сломанные строки. Каждый круг логируется со стоимостью.
"""
from __future__ import annotations

from .check import check_source
from .fix import suggest_patches
from .parser import PickSyntaxError, parse


def repair(source: str, registry: dict | None = None, max_rounds: int = 3) -> dict:
    """Возвращает {source, rounds, log, ok}. Чинит без модели всё, что чинится патчами."""
    log: list[str] = []
    current = source
    for round_no in range(1, max_rounds + 1):
        try:
            parse(current)
            diags = [d for d in check_source(current, registry) if d["code"] in ("UNKNOWN_FN", "UNDEFINED_VAR", "ARITY")]
            if not diags:
                return {"source": current, "rounds": round_no - 1, "log": log, "ok": True}
        except PickSyntaxError:
            diags = []
        patches = suggest_patches(current, registry)
        if not patches:
            log.append(f"круг {round_no}: патчей нет, нужна модель")
            return {"source": current, "rounds": round_no, "log": log, "ok": False}
        from .fix import fix_source
        current = fix_source(current, registry)
        log.append(f"круг {round_no}: применено патчей {len(patches)}")
    try:
        parse(current)
        ok = not [d for d in check_source(current, registry) if d["code"] == "UNKNOWN_FN"]
    except PickSyntaxError:
        ok = False
    return {"source": current, "rounds": max_rounds, "log": log, "ok": ok}


def broken_lines(source: str, registry: dict | None = None) -> list[int]:
    """Номера строк для компактного запроса модели: чинить только это, не всю программу."""
    lines: set[int] = set()
    try:
        parse(source)
    except PickSyntaxError as e:
        lines.add(e.line)
    for d in check_source(source, registry) if _parseable(source) else []:
        if d["line"]:
            lines.add(d["line"])
    return sorted(lines)


def _parseable(source: str) -> bool:
    try:
        parse(source)
        return True
    except PickSyntaxError:
        return False
