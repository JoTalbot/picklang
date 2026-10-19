#!/usr/bin/env python3
"""Краш-тест кластерного уровня: воркер умирает, пока workflow ждёт сигнал.

Воркер-1 поднимается, workflow стартует и встаёт на @wait. Воркера-1 убиваем.
Поднимаем воркера-2 (другой процесс), шлём сигнал — workflow обязан дойти
до конца на воркере-2: состояние живёт в истории Temporal, а не в процессе.

Запуск: python3 tools/temporal_restart.py   (worker — служебный режим)
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
QUEUE = "pick-restart"


async def run_worker(target: str):
    from temporalio.client import Client
    from temporalio.worker import Worker
    from durable_weave.temporal_adapter import HOST_REGISTRY, PickWorkflow, pick_call
    HOST_REGISTRY.update({"bank.charge": lambda user, amt: {"ok": True, "tx": f"tx-{os.getpid()}"}})
    client = await Client.connect(target)
    async with Worker(client, task_queue=QUEUE, workflows=[PickWorkflow], activities=[pick_call]):
        print(f"worker ready pid={os.getpid()}", flush=True)
        await asyncio.Event().wait()


def spawn_worker(target: str) -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen([sys.executable, str(Path(__file__)), "worker", target],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    line = proc.stdout.readline() if proc.stdout else ""
    if "worker ready" not in line:
        proc.terminate()
        raise RuntimeError(f"воркер не поднялся: {line[:200]}")
    return proc, int(line.split("pid=")[1].strip())


async def amain() -> int:
    try:
        from temporalio.testing import WorkflowEnvironment
    except ImportError:
        print("SKIP: temporalio не установлен")
        return 0
    from durable_weave.temporal_adapter import PickWorkflow
    from picklang.ir import compile_ir

    src = "> t(user)\n@wait approval\np = bank.charge(user, 5)\nout p.tx\n"
    try:
        async with await WorkflowEnvironment.start_local() as env:
            target = env.client.config()["service_client"].config.target_host
            w1, pid1 = spawn_worker(target)
            handle = await env.client.start_workflow(
                PickWorkflow.run, args=[compile_ir(src), {"user": "u1"}],
                id="pick-restart-1", task_queue=QUEUE)
            await asyncio.sleep(2)  # workflow дошёл до @wait на воркере-1
            w1.terminate(); w1.wait(timeout=10)
            print(f"воркер-1 (pid={pid1}) убит, workflow ждёт сигнал без воркера", flush=True)
            w2, pid2 = spawn_worker(target)
            try:
                await handle.signal(PickWorkflow.pick_signal, {"name": "approval", "value": True})
                result = await handle.result()
            finally:
                w2.terminate()
        ok = result == f"tx-{pid2}" and pid1 != pid2
        print(f"{'PASS' if ok else 'FAIL'}: после рестарта воркера ({pid1} -> {pid2}) workflow завершился, результат: {result}")
        return 0 if ok else 1
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {str(e)[:500]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "worker":
        asyncio.run(run_worker(sys.argv[2]))
    else:
        raise SystemExit(asyncio.run(amain()))
