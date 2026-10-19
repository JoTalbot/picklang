"""Async-исполнитель: те же гарантии, нативное asyncio вместо потоков.

Одно ядро с sync-исполнителем: журнал, replay со снимков, коммит шага и
verify наследуются от DurableExecutor; своего здесь только await-вызовы
и gather-frontier. Коммиты frontier — в объявленном порядке, не в порядке
финиша (digest исполнения от гонок не зависит).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from .executor import DurableExecutor


class AsyncDurableExecutor(DurableExecutor):
    def __init__(self, workdir: str | Path, handlers: dict[str, Callable],
                 snapshot_every: int | None = None):
        super().__init__(workdir, handlers, storage="sqlite", snapshot_every=snapshot_every)

    def _cached(self, step_id: str):
        stored = self.cas.get_json(self._completed[step_id])
        return stored.get("result") if isinstance(stored, dict) and "result" in stored else stored

    async def call(self, name: str, payload: dict, idempotency_key: str):
        assert self.log is not None
        step_id = f"{name}:{idempotency_key}"
        if step_id in self._completed:
            return self._cached(step_id)
        result = await self.handlers[name]({**payload, "_idempotency_key": idempotency_key})
        self._commit_activity(step_id, name, payload, result, idempotency_key)
        return result

    async def parallel(self, calls: list[tuple[str, dict, str]]) -> list[Any]:
        results: list[Any] = [None] * len(calls)
        pending: dict[int, tuple[str, dict, str]] = {}
        for i, (name, payload, key) in enumerate(calls):
            step_id = f"{name}:{key}"
            if step_id in self._completed:
                results[i] = self._cached(step_id)
            else:
                pending[i] = (name, payload, key)
        if pending:
            async def run_one(item):
                i, (name, payload, key) = item
                return i, await self.handlers[name]({**payload, "_idempotency_key": key})
            done = dict(await asyncio.gather(*(run_one(it) for it in pending.items())))
            for i in pending:  # коммиты — строго в объявленном порядке
                name, payload, key = pending[i]
                self._commit_activity(f"{name}:{key}", name, payload, done[i], key)
                results[i] = done[i]
        return list(results)

    def verify(self, execution_id: str) -> bool:  # type: ignore[override]
        return bool(super().verify(execution_id)["verified"])
