"""verify(): несуществующее исполнение не может быть verified (аудит 19.10.2026)."""
from __future__ import annotations

from durable_weave import DurableExecutor


def test_verify_reports_missing_execution(tmp_path):
    executor = DurableExecutor(tmp_path, {"inc": lambda p: 1})
    info = executor.verify("no-such-execution")
    assert info["verified"] is False
    assert info["status"] == "MISSING"
    assert info["events"] == 0
    # проверка несуществующего исполнения не должна создавать его каталог
    assert not (tmp_path / "executions" / "no-such-execution").exists()


def test_verify_empty_execution_is_not_verified(tmp_path):
    executor = DurableExecutor(tmp_path, {})
    executor.run("empty", lambda ctx, inp: None, {})
    info = executor.verify("empty")
    assert info["events"] > 0 and info["verified"] is True
