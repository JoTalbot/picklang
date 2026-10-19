"""П.4 — Async-исполнитель: те же гарантии, нативное asyncio вместо потоков.

Активности — async-функции; параллельный frontier — asyncio.gather,
коммиты результатов в журнал — в объявленном порядке (digest стабилен).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from .cas import ContentAddressedStore
from .eventlog import EventLog


class AsyncDurableExecutor:
    def __init__(self, workdir: str | Path, handlers: dict[str, Callable]):
        self.workdir = Path(workdir)
        self.handlers = handlers
        self.cas = ContentAddressedStore(self.workdir / "cas")
        self.log: EventLog | None = None
        self._completed: dict[str, str] = {}

    def _load(self, execution_id: str):
        self.log = EventLog(self.workdir / "executions" / execution_id / "events.jsonl")
        self._completed = {ev["data"]["step"]: ev["data"]["result_ref"]
                           for ev in self.log.events if ev["type"] == "ActivityCompleted"}

    async def call(self, name: str, payload: dict, idempotency_key: str):
        assert self.log is not None
        step_id = f"{name}:{idempotency_key}"
        if step_id in self._completed:
            stored = self.cas.get_json(self._completed[step_id])
            return stored.get("result") if isinstance(stored, dict) else stored
        result = await self.handlers[name]({**payload, "_idempotency_key": idempotency_key})
        ref = self.cas.put_json({"activity": name, "step": step_id, "result": result})
        self.log.append("ActivityCompleted", {"step": step_id, "activity": name, "result_ref": ref})
        self._completed[step_id] = ref
        return result

    async def parallel(self, calls: list[tuple[str, dict, str]]) -> list[Any]:
        results = await asyncio.gather(*(self.call(n, p, k) for n, p, k in calls))
        return list(results)

    def verify(self, execution_id: str) -> bool:
        self._load(execution_id)
        assert self.log is not None
        ok, _ = self.log.verify_chain()
        return ok
