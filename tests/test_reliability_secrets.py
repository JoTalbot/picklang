"""Секреты в durable-режиме: replay не требует окружения, значение не попадает на диск."""
from __future__ import annotations

from durable_weave.pickrun import run_pick


def test_secret_replay_does_not_require_environment(tmp_path, monkeypatch):
    """Завершённый шаг переигрывается без чтения секрета: переменной может уже не быть."""
    monkeypatch.setenv("AUDIT_RESUME_KEY", "sk-test-1")
    calls: list[str] = []

    def charge(user, token):
        calls.append(token)
        return {"ok": True, "tx": "T1"}

    source = '> t(u)\nx = charge(u, "secret:AUDIT_RESUME_KEY")\nout x.tx\n'
    registry = {"charge": charge}

    first, _ = run_pick(source, registry, {"u": "u1"}, tmp_path, "s-1", crash_after=1)
    assert first.status == "CRASHED"
    monkeypatch.delenv("AUDIT_RESUME_KEY")
    resumed, _ = run_pick(source, registry, {"u": "u1"}, tmp_path, "s-1")
    assert resumed.status == "COMPLETED", resumed.error
    # обработчик отработал один раз и получил РАЗВЁРНУТЫЙ секрет
    assert calls == ["sk-test-1"]


def test_secret_value_never_hits_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DISK_KEY", "sk-disk-secret")
    source = '> t()\nx = charge("secret:AUDIT_DISK_KEY")\nout x\n'
    run_pick(source, {"charge": lambda token: {"ok": True}}, {}, tmp_path, "s-2")
    leaks = [str(p) for p in tmp_path.rglob("*") if p.is_file() and b"sk-disk-secret" in p.read_bytes()]
    assert leaks == []
