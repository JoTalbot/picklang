"""П.12 — Роутер моделей: фолбэк, таймауты, учёт фактической стоимости задачи.

Живой прогон показал реальность бесплатных/дешёвых моделей: зависания и 429.
Роутер перебирает модели с индивидуальным таймаутом, суммирует usage всех
попыток (включая неудачные по времени — провайдер их всё равно может списать)
и возвращает, какая модель реально ответила.
"""
from __future__ import annotations

from typing import Any

from .live_agent import LLMError, chat


class ModelRouter:
    def __init__(self, models: list[str], per_model_timeout: int = 60, max_completion_tokens: int | None = None,
                 reasoning_max_tokens: int | None = None):
        self.models = models
        self.timeout = per_model_timeout
        self.max_completion_tokens = max_completion_tokens
        self.reasoning_max_tokens = reasoning_max_tokens
        self.attempts: list[dict[str, Any]] = []
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0}

    def chat(self, key: str, messages: list[dict]) -> tuple[str, str, dict]:
        last_error = None
        for model in self.models:
            try:
                text, usage = chat(key, model, messages, timeout=self.timeout,
                                   max_completion_tokens=self.max_completion_tokens,
                                   reasoning_max_tokens=self.reasoning_max_tokens)
                if not text.strip():
                    raise LLMError("пустой ответ")
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    self.total_usage[k] += int(usage.get(k) or 0)
                self.total_usage["reasoning_tokens"] += int(
                    (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
                self.attempts.append({"model": model, "ok": True, "usage": usage})
                return model, text, usage
            except Exception as e:
                self.attempts.append({"model": model, "ok": False, "error": str(e)[:200]})
                last_error = e
        raise LLMError(f"все модели недоступны: {last_error}")

    def report(self) -> dict[str, Any]:
        answered = next((a["model"] for a in reversed(self.attempts) if a["ok"]), None)
        return {"answered_by": answered, "attempts": self.attempts, "total_usage": self.total_usage}
