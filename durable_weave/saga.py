"""Saga-помощник в стиле Temporal: компенсации регистрируются по ходу шагов
и при сбое выполняются в обратном порядке (LIFO) — тоже как активности,
то есть с коммитом каждого результата и теми же гарантиями resume."""
from __future__ import annotations

from typing import Any


class Saga:
    def __init__(self, ctx):
        self._ctx = ctx
        self._compensations: list[tuple[str, dict[str, Any], str]] = []

    def add_compensation(self, activity: str, payload: dict[str, Any], idempotency_key: str) -> None:
        self._compensations.append((activity, payload, idempotency_key))

    def compensate(self) -> list[str]:
        done = []
        for activity, payload, key in reversed(self._compensations):
            self._ctx.execute_activity(activity, payload, idempotency_key=key)
            done.append(activity)
        return done
