"""Один писатель на исполнение: конкурентный resume не дублирует сайд-эффекты."""
from __future__ import annotations

import pytest

from durable_weave import DurableExecutor
from durable_weave.locking import ExecutionBusy, execution_lock


def test_concurrent_run_returns_busy_and_keeps_side_effects_single(tmp_path):
    calls: list[int] = []

    def pay(payload):
        calls.append(1)
        return {"ok": True}

    executor = DurableExecutor(tmp_path, {"pay": pay})

    def wf(ctx, inp):
        ctx.execute_activity("pay", {"args": [1]}, idempotency_key="p1")
        return "done"

    with execution_lock(tmp_path, "c1"):
        blocked = executor.run("c1", wf, {})
    assert blocked.status == "BUSY"
    assert calls == []

    completed = executor.run("c1", wf, {})
    assert completed.status == "COMPLETED"
    assert calls == [1]

    replay = executor.run("c1", wf, {})
    assert replay.status == "COMPLETED" and calls == [1]


def test_lock_is_released_even_after_failure(tmp_path):
    def boom(ctx, inp):
        raise RuntimeError("сбой workflow")

    executor = DurableExecutor(tmp_path, {})
    assert executor.run("f1", boom, {}).status == "FAILED"
    with execution_lock(tmp_path, "f1"):
        pass  # блокировка освобождена падающим запуском
    with execution_lock(tmp_path, "f1"), pytest.raises(ExecutionBusy):  # noqa: SIM117 - вложенность здесь смысл теста
        with execution_lock(tmp_path, "f1"):
            pass
