"""П.2 — Ошибки в формате готового патча.

Вместо «вот текст ошибки, перепиши программу» модель получает применимый
diff в формате diff.py: одна операция = одно исправление = минимум токенов.
"""
from __future__ import annotations

import re

from .check import check_source
from .ir import program_digest
from .parser import PickSyntaxError, parse


def suggest_patches(source: str, registry: dict | None = None) -> list[dict]:
    """Возвращает [{patch, reason}] — готовые к apply_patch строки патча."""
    patches: list[dict] = []
    lines = source.splitlines()
    # 1) Синтаксис: нормализуем частые поломки построчно.
    try:
        parse(source)
    except PickSyntaxError as e:
        if 1 <= e.line <= len(lines):
            raw = lines[e.line - 1]
            fixed = _normalize_line(raw)
            if fixed and fixed != raw.strip():
                patches.append({"patch": f"~{e.line}: {fixed}", "reason": e.hint})
        return patches  # сначала чиним синтаксис, семантику — следующим кругом
    # 2) Семантика по статическому анализу.
    for d in check_source(source, registry):
        line = d["line"]
        if d["code"] == "UNKNOWN_FN" and d.get("suggestion") and 1 <= line <= len(lines):
            bad = d["message"].split("'")[1]
            patches.append({"patch": f"~{line}: {lines[line-1].replace(bad, d['suggestion'])}",
                            "reason": f"{bad} -> {d['suggestion']}?"})
        elif d["code"] == "NO_OUT":
            patches.append({"patch": f"+{len(lines)}: out 0", "reason": "добавить out (заглушка для уточнения)"})
    return patches


def _normalize_line(raw: str) -> str | None:
    line = raw.strip()
    if line.startswith("?") and "->" not in line and "fail" in line:
        cond, _, reason = line[1:].partition("fail")
        return f'? {cond.strip()} -> fail {reason.strip()}'
    if line.startswith("@wait"):
        name = line[5:].strip().split()[0] if line[5:].strip() else "approval"
        return f"@wait {name}"
    if line.startswith(">") and "(" not in line:
        return f"> {line[1:].strip()}()"
    if line.startswith("?") and "->" in line and "fail" not in line:
        cond = line[1:].split("->")[0].strip()
        return f'? {cond} -> fail "условие не выполнено"'
    return None


def fix_source(source: str, registry: dict | None = None) -> str:
    """Применить все предложенные патчи. Digest базы проверяется внутри apply_patch."""
    from .diff import apply_patch
    result = source
    for item in suggest_patches(result, registry):
        try:
            digest = program_digest(result)
        except Exception:
            digest = None  # сломанная база: digest проверить нельзя, чиним без него
        result = apply_patch(result, item["patch"] + "\n", expected_digest=digest)
    return result
