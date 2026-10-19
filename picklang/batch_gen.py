"""Пакетная генерация: несколько программ одним вызовом модели.

Очередь независимых подзадач не обязана стоить по вызову на задачу:
модель возвращает JSON со списком программ, валидатор проверяет каждую
по отдельности, провалившиеся догенерируются одиночными вызовами.
Экономия — на числе roundtrip'ов и на повторной отправке статичного
префикса грамматики, который теперь один на весь пакет.
"""
from __future__ import annotations

from typing import Callable

from .live_agent import chat, extract_json


def batch_generate(key: str, model: str, prompts: list[str],
                     validators: list[Callable[[str], bool]], timeout: int = 60,
                     reasoning_max_tokens: int | None = 512,
                     max_completion_tokens: int | None = 2048) -> list[dict]:
    combined = (
        "Реши все задачи. Верни СТРОГО JSON вида {\"programs\": [\"<код задачи 1>\", \"<код задачи 2>\"]}, "
        "по одной программе на задачу, в том же порядке, без пояснений вне JSON.\n\n"
        + "\n\n".join(f"=== Задача {i + 1} ===\n{p}" for i, p in enumerate(prompts))
    )
    results: list[dict] = [{"ok": False, "text": None, "source": "none"} for _ in prompts]
    try:
        text, usage = chat(key, model, [{"role": "user", "content": combined}], timeout=timeout,
                           max_completion_tokens=max_completion_tokens,
                           reasoning_max_tokens=reasoning_max_tokens)
        programs = extract_json(text).get("programs", [])
    except Exception:
        programs, usage = [], {}
    for i, prog in enumerate(programs[:len(prompts)]):
        if isinstance(prog, str) and validators[i](prog):
            results[i] = {"ok": True, "text": prog, "source": "batch", "usage": usage}
    # догенерация провалившихся по одному — пакет не должен ронять всю очередь
    for i, row in enumerate(results):
        if row["ok"]:
            continue
        try:
            text, usage1 = chat(key, model, [{"role": "user", "content": prompts[i]}], timeout=timeout,
                                max_completion_tokens=max_completion_tokens,
                                reasoning_max_tokens=reasoning_max_tokens)
            if validators[i](text):
                results[i] = {"ok": True, "text": text, "source": "single", "usage": usage1}
        except Exception:
            pass
    return results
