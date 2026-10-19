"""П.9 — Стриминговый разбор намерения: ловим ошибки до конца генерации."""
from __future__ import annotations

import json
from typing import Any


class StreamingIntentParser:
    def __init__(self):
        self.buf = ""
        self.early_error: str | None = None
        self.intent: dict[str, Any] | None = None

    def feed(self, chunk: str) -> dict[str, Any] | None:
        self.buf += chunk
        stripped = self.buf.lstrip().lstrip("`")
        if stripped and not stripped.startswith("{") and "{" not in stripped[:20]:
            self.early_error = "ответ модели не начинается с JSON-объекта"
            return None
        try:
            self.intent = json.loads(self.buf.strip().strip("`"))
            return self.intent
        except json.JSONDecodeError:
            return None  # ещё печатается — не ошибка

    @property
    def complete(self) -> bool:
        return self.intent is not None
