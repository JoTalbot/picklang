"""П.5 — Контекстный компилятор: системный промпт собирается, а не пишется.

Карточка задачи + выбранные инструменты (tool RAG) + блоки словаря ->
один детерминированный промпт с digest: одинаковый ввод — одинаковый хэш,
никаких ручных простыней, которые расходятся между агентами.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def compile_context(task: str, contracts: dict[str, dict], vocab_aliases: list[str] | None = None,
                    done_when: list[str] | None = None) -> dict[str, Any]:
    lines = ["Ты пишешь намерение для генератора программ PICK. Верни только JSON намерения.",
             f"Задача: {task}", "Доступные функции (других нет):"]
    for name, spec in contracts.items():
        args = ", ".join(spec.get("args", []))
        ret = ", ".join(spec.get("returns", []))
        lines.append(f"- {name}({args}) -> {{{ret}}}" + (f" — {spec['description']}" if spec.get("description") else ""))
    if vocab_aliases:
        lines.append("Блоки словаря (use): " + ", ".join(vocab_aliases))
    if done_when:
        lines.append("Готово когда: " + "; ".join(done_when))
    prompt = "\n".join(lines)
    return {"prompt": prompt,
            "digest": hashlib.sha256(prompt.encode()).hexdigest(),
            "tokens": _tokens(prompt)}


def _tokens(text: str) -> int:
    from .result_guard import estimate_tokens
    return estimate_tokens(text)
