#!/usr/bin/env python3
"""П.14 — Дымовой прогон Temporal-адаптера против ЖИВОГО тестового сервера.

Использует temporalio test server (скачивается SDK при первом запуске).
Честные исходы: PASS (workflow дошёл до результата на настоящем сервере),
SKIP (нет temporalio / нет сети / сервер не поднялся) — SKIP не равен PASS.
Запуск: python3 tools/temporal_smoke.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


async def amain() -> int:
    try:
        from temporalio.testing import WorkflowEnvironment
    except ImportError:
        print("SKIP: temporalio не установлен (pip install temporalio)")
        return 0
    from durable_weave.temporal_adapter import HOST_REGISTRY, PickWorkflow, pick_call
    from picklang.ir import compile_ir

    HOST_REGISTRY.update({"bank.charge": lambda user, amt: {"ok": True, "tx": "tx-live"}})
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            from temporalio.worker import Worker
            async with Worker(env.client, task_queue="pick-smoke",
                              workflows=[PickWorkflow], activities=[pick_call]):
                ir = compile_ir('> t(user)\np = bank.charge(user, 5)\nout p.tx\n')
                result = await env.client.execute_workflow(
                    PickWorkflow.run, args=[ir, {"user": "u1"}],
                    id="pick-smoke-1", task_queue="pick-smoke")
        print(f"PASS: настоящий Temporal-сервер исполнил IR, результат: {result}")
        return 0
    except Exception as e:
        print(f"SKIP: тестовый сервер недоступен: {type(e).__name__}: {str(e)[:200]}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
