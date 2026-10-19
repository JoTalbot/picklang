"""Связка PICK -> durable_weave: каждая функция программы становится активностью.

При переигрывании (resume) интерпретатор честно проходит программу от начала,
но завершённые вызовы возвращает DurableExecutor из журнала+CAS — хост-функция
(банк, почта, LLM) второй раз НЕ дёргается. Ожидание сигнала PICK (@wait)
превращается в durable-паузу исполнения: процесс можно убить, сигнал подать
отдельной командой, исполнение продолжится.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from picklang.interpreter import WaitingSignal, run_program

from .executor import DurableExecutor


def make_executor(workdir, registry, crash_after=None) -> DurableExecutor:
    handlers = {}
    for name, fn in registry.items():
        def make(fn):
            return lambda payload: fn(*payload.get("args", []))
        handlers[name] = make(fn)
    return DurableExecutor(Path(workdir), handlers, crash_after=crash_after)


def run_pick(source: str, registry, inputs, workdir, execution_id: str,
             crash_after: int | None = None):
    executor = make_executor(workdir, registry, crash_after=crash_after)

    def workflow(ctx, inp):
        def hook(name, fn, args):
            return ctx.execute_activity(name, {"args": args}, idempotency_key=name)
        try:
            output, _env, _trace = run_program(
                source, registry, inp,
                signal_provider=lambda s: executor._signals.get(s),
                activity_hook=hook,
            )
        except WaitingSignal as wait:
            return ctx.wait_signal(wait.signal)  # -> WorkflowWaiting внутри executor.run
        return output

    return executor.run(execution_id, workflow, inputs), executor
