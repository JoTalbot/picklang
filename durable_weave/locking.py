"""Небольшая утилита блокировки исполнения.

Два процесса, одновременно возобновляющих одно и то же исполнение, — реальный
сценарий (перезапуск после падения + ручной resume, две реплики воркера).
Без блокировки оба переигрывают журнал до первого незакоммиченного шага и
ОБА выполняют его обработчик: оплата проходит дважды.

Блокировка — эксклюзивный flock на файл исполнения. Она не мешает
последовательному resume (файл освобождается при выходе) и не требует
отдельного сервиса.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows: блокировка недоступна
    fcntl = None


class ExecutionBusy(RuntimeError):
    """Исполнение уже выполняется другим процессом."""


@contextmanager
def execution_lock(workdir: Path, execution_id: str) -> Iterator[None]:
    """Эксклюзивная блокировка исполнения; при занятости — ExecutionBusy.

    Блокировка берётся неблокирующе: вызывающая сторона сама решает, подождать
    и повторить (durable-очередь) или вернуть управление.
    """
    if fcntl is None:  # pragma: no cover
        yield
        return
    lock_path = Path(workdir) / "executions" / execution_id / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")  # noqa: SIM115 - живёт ровно один вызов
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExecutionBusy(
                f"исполнение {execution_id} уже выполняется другим процессом"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
