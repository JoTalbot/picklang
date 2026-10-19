"""П.7 — Каскад моделей: сначала маленькая и быстрая, большая — только при провале проверки."""
from __future__ import annotations

from typing import Any, Callable

from .live_agent import chat


class ModelCascade:
    def __init__(self, tiers: list[list[str]], per_model_timeout: int = 45):
        self.tiers = tiers  # [[дешёвые...], [сильные...]]
        self.timeout = per_model_timeout
        self.log: list[dict[str, Any]] = []

    def run(self, key: str, messages: list[dict], validator: Callable[[str], bool],
            json_schema: dict | None = None) -> dict[str, Any]:
        for tier_no, models in enumerate(self.tiers):
            for model in models:
                try:
                    text, usage = chat(key, model, messages, timeout=self.timeout, json_schema=json_schema)
                    ok = bool(text.strip()) and validator(text)
                except Exception as e:
                    self.log.append({"tier": tier_no, "model": model, "ok": False, "error": str(e)[:120]})
                    continue
                self.log.append({"tier": tier_no, "model": model, "ok": ok, "usage": usage})
                if ok:
                    return {"model": model, "tier": tier_no, "text": text, "usage": usage, "log": self.log}
        raise RuntimeError(f"каскад исчерпан: {self.log}")
