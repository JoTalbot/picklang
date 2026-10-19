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
    if estimate_tokens(value) <= max_tokens:
        return value
    ref = cas.put_json(value) if cas is not None else None
    preview = value
    if isinstance(value, list):
        preview = [_shallow(v) for v in value[:3]]
    elif isinstance(value, dict):
        preview = {k: value[k] for k in list(value)[:5]}
    elif isinstance(value, str):
        preview = value[:400] + "…"
    result = {"truncated": True, "preview": preview, "tokens_full": estimate_tokens(value)}
    if ref:
        result["ref"] = ref
    # preview тоже может оказаться большой — добиваем грубо, но честно помечаем
    while estimate_tokens(result) > max_tokens and isinstance(preview, list) and preview:
        preview = preview[:-1]
        result["preview"] = preview
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
