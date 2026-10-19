"""П.1 — Лимиты на результаты инструментов.

Обёртка над реестром хоста: если результат вызова больше бюджета, в программу
возвращается компактная форма {truncated, ref?, preview}, а полный результат
(при заданном CAS) складывается по digest — развернуть можно отдельным шагом,
осознанно, а не «весь поиск в контекст по умолчанию».
"""
from __future__ import annotations

import json
from typing import Any, Callable


from functools import lru_cache


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def estimate_tokens_fast(value: Any) -> int:
    """Грубая оценка без BPE-кодирования: ~4 символа на токен.

    Для программ без токен-бюджета точный подсчёт на каждый вызов — чистый
    оверхед (BPE дороже самой логики шага); калибровка по cl100k даёт
    ошибку в пределах десятков процентов, для статистики этого хватает.
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return max(1, len(text) // 4)


def estimate_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False, default=str)
    enc = _encoding()
    return len(enc.encode(text)) if enc is not None else max(1, len(text) // 4)


def _shallow(v):
    if isinstance(v, dict):
        return {k: (x[:80] + "…" if isinstance(x, str) and len(x) > 80 else x) for k, x in list(v.items())[:5]}
    if isinstance(v, str) and len(v) > 80:
        return v[:80] + "…"
    return v


def compact_result(value: Any, max_tokens: int, cas=None) -> Any:
    """Return the original value when it fits, otherwise a strictly bounded preview.

    The complete value is stored before compaction when a CAS is available. Preview
    compaction is shape-independent: large nested dictionaries and strings must not
    bypass the budget merely because the top-level value is not a list.
    """
    if max_tokens < 1:
        raise ValueError("max_tokens must be >= 1")
    full_tokens = estimate_tokens(value)
    if full_tokens <= max_tokens:
        return value

    ref = cas.put_json(value) if cas is not None else None
    if isinstance(value, list):
        preview: Any = [_shallow(v) for v in value[:3]]
    elif isinstance(value, dict):
        preview = {k: _shallow(v) for k, v in list(value.items())[:5]}
    elif isinstance(value, str):
        preview = value[:400]
    else:
        preview = _shallow(value)

    result = {"truncated": True, "preview": preview, "tokens_full": full_tokens}
    if ref:
        result["ref"] = ref

    # Preserve a useful structured preview when possible. If nested values keep
    # it over budget, switch to a serialized snippet and shrink that deterministically.
    if estimate_tokens(result) > max_tokens:
        try:
            snippet = json.dumps(preview, ensure_ascii=False, default=str)
        except Exception:
            snippet = str(preview)
        result["preview"] = snippet
        while snippet and estimate_tokens(result) > max_tokens:
            snippet = snippet[: max(0, len(snippet) // 2)]
            result["preview"] = snippet

    # Metadata itself can exceed extremely small budgets (notably a long CAS ref).
    # Drop optional fields in order, then fall back to the smallest useful marker.
    if estimate_tokens(result) > max_tokens:
        result.pop("tokens_full", None)
    if estimate_tokens(result) > max_tokens:
        result.pop("ref", None)
    if estimate_tokens(result) > max_tokens:
        result.pop("preview", None)
    if estimate_tokens(result) > max_tokens:
        return {"truncated": True}
    return result



class ToolResultGuard:
    def __init__(self, registry: dict[str, Callable], max_tokens: int = 300, cas=None):
        self.max_tokens, self.cas = max_tokens, cas
        self.registry = {name: self._wrap(fn) for name, fn in registry.items()}
        self.stats = {"calls": 0, "truncated": 0, "tokens_saved": 0}

    def _wrap(self, fn):
        def call(*args):
            result = fn(*args)
            self.stats["calls"] += 1
            full = estimate_tokens(result)
            compacted = compact_result(result, self.max_tokens, self.cas)
            if isinstance(compacted, dict) and compacted.get("truncated"):
                self.stats["truncated"] += 1
                self.stats["tokens_saved"] += full - estimate_tokens(compacted)
            return compacted
        return call
