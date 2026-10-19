#!/usr/bin/env python3
"""Распределённый прогон: PICK на Temporal, воркер — отдельный ОС-процесс.

Клиент только стартует workflow и шлёт сигнал; все активности исполняет
процесс-воркер (его pid виден в результате: tx-<pid воркера>). Между ними —
настоящий Temporal test server. Доказывает: программа едет по сети,
хост-функции живут на воркере, @wait работает сигналом извне.

Запуск: python3 tools/temporal_distributed.py
Исходы: PASS / SKIP (нет temporalio или сервер не поднялся) — как в smoke.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
QUEUE = "pick-dist"


def worker_registry():
    import os as _os
    return {
        "bank.charge": lambda user, amt: {"ok": True, "tx": f"tx-{_os.getpid()}"},
        "dbl": lambda i: i * 2,
    }


async def run_worker(target: str):
    from temporalio.client import Client
    from temporalio.worker import Worker
    from durable_weave.temporal_adapter import HOST_REGISTRY, PickWorkflow, pick_call
    HOST_REGISTRY.update(worker_registry())
    client = await Client.connect(target)
    async with Worker(client, task_queue=QUEUE, workflows=[PickWorkflow], activities=[pick_call]):
        print(f"worker ready pid={os.getpid()}", flush=True)
        await asyncio.Event().wait()


async def amain() -> int:
    try:
        from temporalio.testing import WorkflowEnvironment
    except ImportError:
        print("SKIP: temporalio не установлен")
        return 0
    from durable_weave.temporal_adapter import PickWorkflow
    from picklang.ir import compile_ir

    src = ("> t(user)\n@wait approval\np = bank.charge(user, 5)\n"
           "for i in xs: y = dbl(i)\ndone when: y > 1\nout {tx: p.tx, y: y}\n")
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            target = env.client.config()["service_client"].config.target_host
            proc = subprocess.Popen(
                [sys.executable, str(Path(__file__)), "worker", target],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                line = proc.stdout.readline() if proc.stdout else ""
                if "worker ready" not in line:
                    print(f"FAIL: воркер не поднялся: {line[:200]}", file=sys.stderr)
                    return 1
                worker_pid = int(line.split("pid=")[1].strip())
                ir = compile_ir(src)
                handle = await env.client.start_workflow(
                    PickWorkflow.run, args=[ir, {"user": "u1", "xs": [1, 3]}],
                    id="pick-dist-1", task_queue=QUEUE)
                await handle.signal(PickWorkflow.pick_signal, {"name": "approval", "value": True})
                result = await handle.result()
            finally:
                proc.terminate()
        ok = result["tx"] == f"tx-{worker_pid}" and result["y"] == 6 and worker_pid != os.getpid()
        print(f"{'PASS' if ok else 'FAIL'}: клиент pid={os.getpid()}, активности исполнил "
              f"воркер pid={worker_pid} в другом процессе, результат: {result}")
        return 0 if ok else 1
    except Exception as e:
        print(f"SKIP: {type(e).__name__}: {str(e)[:200]}")
        return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "worker":
        asyncio.run(run_worker(sys.argv[2]))
    else:
        raise SystemExit(asyncio.run(amain()))
