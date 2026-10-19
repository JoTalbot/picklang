"""Durable-исполнитель в духе Temporal, но без сервера: workflow — обычный Python-код,
который при возобновлении переигрывается от начала, а завершённые активности НЕ
выполняются заново — их результаты берутся из журнала + CAS.

Гарантии и честные границы:
- переживает падение процесса между активностями (результат закоммичен до следующего шага);
- НЕ даёт exactly-once внешних сайд-эффектов сам по себе: обработчик активности обязан
  быть идемпотентным по idempotency_key (как Activities в Temporal);
- каждое завершение активности: результат -> CAS, событие в хэш-цепочку, digest растёт.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .cas import ContentAddressedStore, canonical_json, sha256_hex
from .sqlite_backend import SQLiteEventLog


class SimulatedCrash(Exception):
    """Искусственное падение «процесса» для демонстрации/тестов resume."""


class WorkflowWaiting(Exception):
    def __init__(self, signal: str):
        super().__init__(f"waiting for signal: {signal}")
        self.signal = signal


@dataclass
class ExecutionResult:
    execution_id: str
    status: str  # COMPLETED | FAILED | WAITING | CRASHED
    output: Any = None
    digest: str = ""
    waiting_for: str | None = None
    error: str | None = None


Handler = Callable[[dict[str, Any]], Any]


def _input_digest(payload: dict) -> str:
    """Хэш входа прямо в событии: отдельный CAS-объект ради digest больше не пишется."""
    return sha256_hex(canonical_json(payload))


class Context:
    """Контекст workflow: единственный легальный способ делать сайд-эффекты и ждать сигналов."""

    def __init__(self, executor: "DurableExecutor", execution_id: str):
        self._ex = executor
        self.execution_id = execution_id
        self._seq = 0

    def execute_activity(self, name: str, payload: dict[str, Any] | None = None,
                         idempotency_key: str | None = None, retries: int = 0) -> Any:
        payload = payload or {}
        seq = self._seq
        self._seq += 1
        step_id = f"{seq}:{name}:{idempotency_key or 'auto'}"
        ex = self._ex
        # Replay: результат уже закоммичен — обработчик НЕ вызываем.
        if step_id in ex._completed:
            stored = ex.cas.get_json(ex._completed[step_id])
            return stored.get("result") if isinstance(stored, dict) and "result" in stored else stored
        handler = ex.handlers.get(name)
        if handler is None:
            raise KeyError(f"activity handler not registered: {name}")
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                result = handler({**payload, "_idempotency_key": idempotency_key, "_execution_id": self.execution_id})
                break
            except SimulatedCrash:
                raise
            except Exception as exc:  # retry policy как в Temporal Activities
                last_exc = exc
                if attempt < retries:
                    time.sleep(min(0.05 * (2 ** attempt), 0.5))
        else:
            raise last_exc  # type: ignore[misc]
        ex._commit_activity(step_id, name, payload, result, idempotency_key)
        return result

    def execute_parallel(self, calls: list[tuple[str, dict, str]]) -> list[Any]:
        """Frontier: независимые активности — одновременно, коммиты — в объявленном порядке.

        Выполнение параллельное, запись результатов в журнал — строго по порядку
        списка: digest исполнения не зависит от того, кто финишировал первым.
        """
        from concurrent.futures import ThreadPoolExecutor
        ex = self._ex
        results: list[Any] = [None] * len(calls)
        pending: dict[int, tuple[str, dict, str]] = {}
        for i, (name, payload, key) in enumerate(calls):
            step_id = f"par{i}:{name}:{key}"
            if step_id in ex._completed:
                stored = ex.cas.get_json(ex._completed[step_id])
                results[i] = stored.get("result") if isinstance(stored, dict) else stored
            else:
                pending[i] = (name, payload, key)
        if pending:
            def run_one(item):
                i, (name, payload, key) = item
                handler = ex.handlers[name]
                return i, handler({**payload, "_idempotency_key": key, "_execution_id": self.execution_id})
            with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                done = dict(pool.map(run_one, pending.items()))
            batch = []
            for i in pending:  # коммиты канонично по объявленному порядку
                name, payload, key = pending[i]
                result = done[i]
                step_id = f"par{i}:{name}:{key}"
                ref = ex.cas.put_json({"activity": name, "step": step_id, "result": result})
                batch.append(("ActivityCompleted", {"step": step_id, "activity": name,
                                                    "idempotency_key": key,
                                                    "input_digest": _input_digest(payload),
                                                    "result_ref": ref}))
                ex._completed[step_id] = ref
                results[i] = result
            # весь frontier — одна транзакция (append_many), а не коммит на событие
            append_many = getattr(ex.log, "append_many", None)
            if append_many is not None:
                append_many(batch)
            else:
                for type_, data in batch:
                    ex.log.append(type_, data)
        return results

    def wait_signal(self, name: str) -> Any:
        if name in self._ex._signals:
            return self._ex._signals[name]
        raise WorkflowWaiting(name)


class DurableExecutor:
    def __init__(self, workdir: str | Path, handlers: dict[str, Handler], crash_after: int | None = None,
                 storage: str = "sqlite", snapshot_every: int | None = None):
        self.workdir = Path(workdir)
        self.handlers = handlers
        self.crash_after = crash_after
        if storage != "sqlite":
            raise ValueError("единственный backend журнала — sqlite (jsonl удалён)")
        self.storage = storage
        self.snapshot_every = snapshot_every  # П.1: снимок completed-карты каждые N шагов
        self.snapshot_used = False
        self.cas = ContentAddressedStore(self.workdir / "cas")
        self.log: SQLiteEventLog | None = None
        self._completed: dict[str, str] = {}
        self._signals: dict[str, Any] = {}
        self._steps_done = 0

    def _commit_activity(self, step_id: str, name: str, payload: dict, result: Any,
                         idempotency_key: str | None = None) -> str:
        """Commit point, общий для sync/async: CAS -> событие -> снимок по расписанию."""
        result_ref = self.cas.put_json({"activity": name, "step": step_id, "result": result})
        assert self.log is not None
        self.log.append("ActivityCompleted", {
            "step": step_id, "activity": name, "idempotency_key": idempotency_key,
            "input_digest": _input_digest(payload), "result_ref": result_ref,
        })
        self._completed[step_id] = result_ref
        self._steps_done += 1
        if self.snapshot_every and self._steps_done % self.snapshot_every == 0:
            snap_ref = self.cas.put_json({"seq": len(self.log.events), "completed": dict(self._completed)})
            self.log.append("StateSnapshot", {"snapshot_ref": snap_ref, "steps": self._steps_done})
        if self.crash_after is not None and self._steps_done >= self.crash_after:
            raise SimulatedCrash(f"simulated crash after {self._steps_done} committed activities")
        return result_ref

    # --- жизненный цикл исполнения -------------------------------------
    def _load(self, execution_id: str) -> None:
        self.log = SQLiteEventLog(self.workdir / "executions" / execution_id / "events.db")
        self._completed = {}
        self._signals = {}
        self.snapshot_used = False
        events = self.log.events
        start = 0
        # П.1: последний снимок даёт completed-карту сразу; обрабатываем только хвост после него
        for i in range(len(events) - 1, -1, -1):
            if events[i]["type"] == "StateSnapshot":
                try:
                    snap = self.cas.get_json(events[i]["data"]["snapshot_ref"])
                    self._completed = dict(snap["completed"])
                    self.snapshot_used = True
                    start = i + 1
                except Exception:
                    pass
                break
        for ev in events[start:]:
            if ev["type"] == "ActivityCompleted":
                self._completed[ev["data"]["step"]] = ev["data"]["result_ref"]
            elif ev["type"] == "SignalReceived":
                self._signals[ev["data"]["name"]] = ev["data"]["payload"]

    def run(self, execution_id: str, workflow: Callable[[Context, Any], Any], input_: Any) -> ExecutionResult:
        self._load(execution_id)
        assert self.log is not None
        if not any(e["type"] == "WorkflowStarted" for e in self.log.events):
            self.log.append("WorkflowStarted", {"workflow": workflow.__name__, "input_ref": self.cas.put_json(input_)})
        ctx = Context(self, execution_id)
        try:
            output = workflow(ctx, input_)
        except WorkflowWaiting as wait:
            self.log.append("WorkflowWaiting", {"signal": wait.signal})
            return ExecutionResult(execution_id, "WAITING", digest=self.log.last_hash, waiting_for=wait.signal)
        except SimulatedCrash as crash:
            self.log.append("ProcessCrashed", {"detail": str(crash)})
            return ExecutionResult(execution_id, "CRASHED", digest=self.log.last_hash, error=str(crash))
        except Exception as exc:
            self.log.append("WorkflowFailed", {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=5)})
            return ExecutionResult(execution_id, "FAILED", digest=self.log.last_hash, error=str(exc))
        output_ref = self.cas.put_json(output) if not isinstance(output, str) else self.cas.put_text(output)
        self.log.append("WorkflowCompleted", {"output_ref": output_ref})
        return ExecutionResult(execution_id, "COMPLETED", output=output, digest=self.log.last_hash)

    def deliver_signal(self, execution_id: str, name: str, payload: Any) -> None:
        self._load(execution_id)
        assert self.log is not None
        # Дубликат сигнала с тем же содержимым — no-op (идемпотентная доставка).
        if self._signals.get(name) == payload and name in self._signals:
            return
        self.log.append("SignalReceived", {"name": name, "payload": payload})

    # --- верификация: то, чего у классического durable-исполнителя нет ----
    def verify(self, execution_id: str) -> dict[str, Any]:
        self._load(execution_id)
        assert self.log is not None
        ok, detail = self.log.verify_chain()
        artifacts_ok, bad = True, []
        for ev in self.log.events:
            ref = ev["data"].get("result_ref") or ev["data"].get("output_ref")
            if ref and not self.cas.verify(ref):
                artifacts_ok, bad = False, bad + [ref]
        status = "unknown"
        for ev in reversed(self.log.events):
            status = {"WorkflowCompleted": "COMPLETED", "WorkflowFailed": "FAILED",
                      "WorkflowWaiting": "WAITING", "ProcessCrashed": "CRASHED"}.get(ev["type"], status)
            if status != "unknown":
                break
        return {
            "execution_id": execution_id, "status": status,
            "chain_ok": ok, "chain_detail": detail,
            "artifacts_ok": artifacts_ok, "bad_artifacts": bad,
            "events": len(self.log.events), "digest": self.log.last_hash,
            "cas": self.cas.stats(), "verified": bool(ok and artifacts_ok),
        }
