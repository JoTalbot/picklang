"""П.4 — Tool RAG: модель видит только релевантные задаче инструменты.

Список всех функций проекта растёт и съедает промпт; по карточке задачи
подбираются top-k по пересечению слов с описаниями/ключами контрактов.
"""
from __future__ import annotations

import re
from typing import Any


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-я0-9_]+", text.lower()))


def select_tools(task: str, contracts: dict[str, dict], top_k: int = 5) -> dict[str, Any]:
    task_words = _words(task)
    scored = []
    for name, spec in contracts.items():
        hay = _words(name + " " + spec.get("description", "") + " " + " ".join(spec.get("keywords", [])))
        score = len(task_words & hay)
        scored.append((score, name))
    scored.sort(reverse=True)
    selected = [name for score, name in scored[:top_k] if score > 0] or [name for _s, name in scored[:top_k]]
    from .result_guard import estimate_tokens
    return {
        "selected": {name: contracts[name] for name in selected},
        "names": selected,
        "tokens_all": estimate_tokens(contracts),
        "tokens_selected": estimate_tokens({n: contracts[n] for n in selected}),
    }
