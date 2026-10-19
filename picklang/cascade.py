"""П.7 — Каскад моделей: сначала маленькая и быстрая, большая — только при провале проверки."""
from __future__ import annotations

from typing import Any, Callable

from .live_agent import chat


class ModelCascade:
    def __init__(self, tiers: list[list[str]], per_model_timeout: int = 45,
                 reasoning_max_tokens: int | None = 512, max_completion_tokens: int | None = 1024):
        self.tiers = tiers  # [[дешёвые...], [сильные...]]
        self.timeout = per_model_timeout
        # живой замер: модели жгли 1.5–5.5k токенов рассуждений на ответ из
        # трёх строк — капы по умолчанию, а не опция, о которой надо вспомнить
        self.reasoning_max_tokens = reasoning_max_tokens
        self.max_completion_tokens = max_completion_tokens
        self.log: list[dict[str, Any]] = []

    def run(self, key: str, messages: list[dict], validator: Callable[[str], bool],
            json_schema: dict | None = None) -> dict[str, Any]:
        for tier_no, models in enumerate(self.tiers):
            for model in models:
                try:
                    text, usage = chat(key, model, messages, timeout=self.timeout, json_schema=json_schema,
                                       max_completion_tokens=self.max_completion_tokens,
                                       reasoning_max_tokens=self.reasoning_max_tokens)
                    ok = bool(text.strip()) and validator(text)
                except Exception as e:
                    self.log.append({"tier": tier_no, "model": model, "ok": False, "error": str(e)[:120]})
                    continue
                self.log.append({"tier": tier_no, "model": model, "ok": ok, "usage": usage})
                if ok:
                    return {"model": model, "tier": tier_no, "text": text, "usage": usage, "log": self.log}
        raise RuntimeError(f"каскад исчерпан: {self.log}")
