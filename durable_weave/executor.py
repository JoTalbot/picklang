"""Durable-исполнитель в духе Temporal, но без сервера: workflow — обычный Python-код,
который при возобновлении переигрывается от начала, а завершённые активности НЕ
выполняются заново — их результаты берутся из журнала + CAS.

Гарантии и честные границы:
- переживает падение процесса между активностями (результат закоммичен до следующего шага);
- НЕ даёт exactly-once внешних сайд-эффектов сам по себе: обработчик активности обязан
  быть идемпотентным по `_idempotency_key` (как Activities в Temporal). Рантайм кладёт
  туда step_id — идентификатор позиции шага («seq:activity:key»), уникальный для
  каждого вызова и стабильный при replay; ключ вызывающего доступен в `_requested_key`;
- каждое завершение активности: результат -> CAS, событие в хэш-цепочку, digest растёт.
"""
from __future__ import annotations

import sqlite3
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .cas import ContentAddressedStore, canonical_json, sha256_hex
from .locking import ExecutionBusy, execution_lock
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
    status: str  # COMPLETED | FAILED | WAITING | CRASHED | BUSY
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
            stored = ex._replay(step_id, payload)
            return stored.get("result") if isinstance(stored, dict) and "result" in stored else stored
        handler = ex.handlers.get(name)
        if handler is None:
            raise KeyError(f"activity handler not registered: {name}")
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                # Документированный контракт: обработчик дедуплицирует по _idempotency_key.
                # Поэтому туда уходит step_id (уникален на позицию программы и стабилен
                # при replay), а не имя активности: две разные операции одного типа
                # (два списания) не должны схлопываться в одну.
                result = handler({**payload, "_idempotency_key": step_id,
                                  "_requested_key": idempotency_key,
                                  "_execution_id": self.execution_id})
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
        ex._check_parallel_layout()
        group = self._seq
        self._seq += 1
        def step(i, name, key):
            return f"par{group}.{i}:{name}:{key}"
        for i, (name, payload, key) in enumerate(calls):
            step_id = step(i, name, key)
            if step_id in ex._completed:
                stored = ex._replay(step_id, payload)
                results[i] = stored.get("result") if isinstance(stored, dict) else stored
            else:
                pending[i] = (name, payload, key)
        if pending:
            def run_one(item):
                i, (name, payload, key) = item
                handler = ex.handlers[name]
                return i, handler({**payload, "_idempotency_key": step(i, name, key),
                                   "_requested_key": key, "_execution_id": self.execution_id})
            with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                done = dict(pool.map(run_one, pending.items()))
            frontier = []
            for i in pending:
                name, payload, key = pending[i]
                frontier.append((step(i, name, key), name, payload, done[i], key))
                results[i] = done[i]
            ex._commit_frontier(frontier)
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
        self._seq_counter = 0        # для async-исполнителя: уникальность step_id внутри запуска

    def _check_parallel_layout(self) -> None:
        if any(key.startswith("par") and "." not in key.split(":", 1)[0]
               for key in self._completed):
            raise ValueError("legacy parallel step IDs require explicit migration before replay")

    def _replay(self, step_id: str, payload: dict) -> Any:
        ref = self._completed[step_id]
        if not self.cas.verify(ref):
            raise ValueError(f"corrupt replay result: {step_id}")
        stored = self.cas.get_json(ref)
        digest = stored.get("input_digest") if isinstance(stored, dict) else None
        if digest is None and self.log is not None:
            digest = next((e["data"].get("input_digest") for e in self.log.events
                           if e["type"] == "ActivityCompleted" and e["data"]["step"] == step_id), None)
        if digest is not None and digest != _input_digest(payload):
            raise ValueError(f"replay input mismatch: {step_id}")
        return stored

    def _commit_activity(self, step_id: str, name: str, payload: dict, result: Any,
                         idempotency_key: str | None = None) -> str:
        """Commit point, общий для sync/async: CAS -> событие -> снимок по расписанию."""
        result_ref = self.cas.put_json({"activity": name, "step": step_id, "input_digest": _input_digest(payload), "result": result})
        assert self.log is not None
        self.log.append("ActivityCompleted", {
            "step": step_id, "activity": name, "idempotency_key": idempotency_key,
            "input_digest": _input_digest(payload), "result_ref": result_ref,
        })
        self._completed[step_id] = result_ref
        self._after_commit(1)
        return result_ref

    def _commit_frontier(self, calls: list[tuple]) -> None:
        """Атомарный frontier sync/async: память меняется только после SQLite commit."""
        assert self.log is not None
        batch = []
        for step_id, name, payload, result, key in calls:
            digest = _input_digest(payload)
            ref = self.cas.put_json({"activity": name, "step": step_id,
                                     "input_digest": digest, "result": result})
            batch.append(("ActivityCompleted", {"step": step_id, "activity": name,
                          "idempotency_key": key, "input_digest": digest, "result_ref": ref}))
        self.log.append_many(batch)
        for _, data in batch:
            self._completed[data["step"]] = data["result_ref"]
        if batch:
            self._after_commit(len(batch))

    def _after_commit(self, count: int) -> None:
        previous = self._steps_done
        self._steps_done += count
        if self.snapshot_every and previous // self.snapshot_every < self._steps_done // self.snapshot_every:
            snap_ref = self.cas.put_json({"seq": len(self.log.events), "completed": dict(self._completed),
                                          "signals": dict(self._signals), "identity": self._identity})
            self.log.append("StateSnapshot", {"snapshot_ref": snap_ref, "steps": self._steps_done})
        if self.crash_after is not None and self._steps_done >= self.crash_after:
            raise SimulatedCrash(f"simulated crash after {self._steps_done} committed activities")

    def _read_snapshot(self, ref: str) -> dict:
        if not self.cas.verify(ref):
            raise ValueError("corrupt snapshot")
        snap = self.cas.get_json(ref)
        if (not isinstance(snap, dict) or not isinstance(snap.get("completed"), dict)
                or not isinstance(snap.get("signals", {}), dict)
                or (snap.get("identity") is not None and not isinstance(snap["identity"], dict))
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in snap["completed"].items())):
            raise ValueError("invalid snapshot schema")
        return snap

    # --- жизненный цикл исполнения -------------------------------------
    def _load(self, execution_id: str) -> None:
        self.log = SQLiteEventLog(self.workdir / "executions" / execution_id / "events.db")
        self._completed = {}
        self._signals = {}
        self._identity = None
        self._steps_done = 0
        self.snapshot_used = False
        self._seq_counter = 0
        events = self.log.events
        start = 0
        # П.1: последний снимок даёт completed-карту сразу; обрабатываем только хвост после него
        for i in range(len(events) - 1, -1, -1):
            if events[i]["type"] == "StateSnapshot":
                try:
                    ref = events[i]["data"]["snapshot_ref"]
                    snap = self._read_snapshot(ref)
                    self._completed = dict(snap["completed"])
                    self._signals = dict(snap.get("signals", {}))
                    self._identity = snap.get("identity")
                    # Старые снимки не содержали сигналы: восстанавливаем префикс.
                    prefix = events[:i + 1]
                    if "signals" not in snap or "identity" not in snap:
                        archived = self.log.db.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive'"
                        ).fetchone()
                        if archived:
                            import json
                            prefix = [{"type": typ, "data": json.loads(data)}
                                      for typ, data in self.log.db.execute(
                                          "SELECT type,data FROM archive WHERE seq <= ? ORDER BY seq",
                                          (events[i]["seq"],))] + prefix
                    for prior in prefix:
                        if prior["type"] == "SignalReceived":
                            self._signals[prior["data"]["name"]] = prior["data"]["payload"]
                        elif prior["type"] == "WorkflowStarted":
                            self._identity = prior["data"]
                    self.snapshot_used = True
                    start = i + 1
                except (KeyError, ValueError, TypeError, OSError):
                    # Снимок — ускорение, не единственный источник истины.
                    self._completed, self._signals, self._identity = {}, {}, None
                    events = self.log.history()
                    start = 0
                break
        for ev in events[start:]:
            if ev["type"] == "WorkflowStarted":
                self._identity = ev["data"]
            elif ev["type"] == "ActivityCompleted":
                self._completed[ev["data"]["step"]] = ev["data"]["result_ref"]
            elif ev["type"] == "SignalReceived":
                self._signals[ev["data"]["name"]] = ev["data"]["payload"]

        self._steps_done = len(self._completed)

    def run(self, execution_id: str, workflow: Callable[[Context, Any], Any], input_: Any,
            *, workflow_version: str | None = None) -> ExecutionResult:
        # Один писатель на исполнение: иначе два процесса переигрывают журнал до
        # первого незакоммиченного шага и оба выполняют его обработчик.
        try:
            with execution_lock(self.workdir, execution_id):
                return self._run_locked(execution_id, workflow, input_, workflow_version)
        except ExecutionBusy as busy:
            return ExecutionResult(execution_id, "BUSY", error=str(busy))
        except (sqlite3.Error, OSError) as exc:
            # Не пытаемся записать сообщение об ошибке в уже сломанное хранилище.
            return ExecutionResult(execution_id, "FAILED", error=f"storage failure: {exc}")

    def _run_locked(self, execution_id: str, workflow: Callable[[Context, Any], Any],
                    input_: Any, workflow_version: str | None = None) -> ExecutionResult:
        self._load(execution_id)
        assert self.log is not None
        chain_ok, detail = self.log.verify_chain()
        if not chain_ok:
            return ExecutionResult(execution_id, "FAILED", error=f"corrupt journal: {detail}")
        identity = {"workflow": workflow.__name__, "input_ref": self.cas.put_json(input_),
                    "workflow_version": workflow_version}
        if self._identity is not None:
            if any(self._identity.get(k) != identity[k] for k in identity):
                return ExecutionResult(execution_id, "FAILED", error="replay workflow/input mismatch")
        else:
            self.log.append("WorkflowStarted", identity)
            self._identity = identity
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
        # Сигнал дописывает журнал: тот же эксклюзивный доступ, что и у run().
        with execution_lock(self.workdir, execution_id):
            self._deliver_signal_locked(execution_id, name, payload)

    def _deliver_signal_locked(self, execution_id: str, name: str, payload: Any) -> None:
        self._load(execution_id)
        assert self.log is not None
        # Дубликат сигнала с тем же содержимым — no-op (идемпотентная доставка).
        if self._signals.get(name) == payload and name in self._signals:
            return
        self.log.append("SignalReceived", {"name": name, "payload": payload})

    # --- верификация: то, чего у классического durable-исполнителя нет ----
    def verify(self, execution_id: str) -> dict[str, Any]:
        db_path = self.workdir / "executions" / execution_id / "events.db"
        if not db_path.exists():
            # Иначе опечатка в id даёт «verified: true» на пустой цепочке и создаёт
            # пустую директорию исполнения как побочный эффект проверки.
            return {"execution_id": execution_id, "status": "MISSING",
                    "chain_ok": False, "chain_detail": "execution not found",
                    "artifacts_ok": False, "bad_artifacts": [],
                    "events": 0, "digest": "", "cas": self.cas.stats(), "verified": False}
        self._load(execution_id)
        assert self.log is not None
        ok, detail = self.log.verify_chain()
        # После compact результаты остаются в archive и/или snapshot.completed.
        # Проверяем все семантические ссылки, а не только output активного окна.
        import json
        refs: set[str] = set()
        snapshots: set[str] = set()
        tables = ["events"]
        if self.log.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive'"
        ).fetchone():
            tables.append("archive")
        for table in tables:
            for (raw,) in self.log.db.execute(f"SELECT data FROM {table}"):
                data = json.loads(raw)
                for key in ("input_ref", "result_ref", "output_ref", "snapshot_ref"):
                    if data.get(key):
                        refs.add(data[key])
                if data.get("snapshot_ref"):
                    snapshots.add(data["snapshot_ref"])
        invalid_snapshots = set()
        for ref in sorted(snapshots):
            try:
                snap = self._read_snapshot(ref)
            except (KeyError, ValueError, TypeError, OSError):
                invalid_snapshots.add(ref)
                continue
            refs.update(snap["completed"].values())
            identity = snap.get("identity") or {}
            if identity.get("input_ref"):
                refs.add(identity["input_ref"])
        bad = sorted(invalid_snapshots | {ref for ref in refs if not self.cas.verify(ref)})
        artifacts_ok = not bad
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
            "cas": self.cas.stats(),
            "verified": bool(ok and artifacts_ok and self.log.events),
        }
