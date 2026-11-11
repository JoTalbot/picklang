import pytest

from durable_weave import ContentAddressedStore, DurableExecutor
from durable_weave.pickrun import run_pick


PAYMENT = open("examples/payment.pick", encoding="utf-8").read()


def test_cas_dedup_and_tamper(tmp_path):
    cas = ContentAddressedStore(tmp_path / "cas")
    a, b = cas.put_text("один и тот же блок шапки релиза"), cas.put_text("один и тот же блок шапки релиза")
    assert a == b and cas.stats()["objects"] == 1
    assert cas.verify(a)
    cas._path(a).write_bytes("подменено".encode())
    assert not cas.verify(a)


def test_cas_fsyncs_object_directory_after_atomic_rename(tmp_path, monkeypatch):
    import durable_weave.cas as cas_module

    events = []
    real_replace = cas_module.os.replace

    def tracked_replace(source, destination):
        events.append("replace")
        real_replace(source, destination)

    def tracked_directory_fsync(path):
        events.append(("directory_fsync", path))

    monkeypatch.setattr(cas_module.os, "replace", tracked_replace)
    monkeypatch.setattr(cas_module, "_fsync_directory", tracked_directory_fsync)

    cas = ContentAddressedStore(tmp_path / "cas")
    events.clear()
    real_mkdir = cas_module.Path.mkdir

    def tracked_mkdir(path, *args, **kwargs):
        was_missing = not path.exists()
        result = real_mkdir(path, *args, **kwargs)
        if was_missing:
            events.append(("mkdir", path))
        return result

    monkeypatch.setattr(cas_module.Path, "mkdir", tracked_mkdir)
    ref = cas.put_bytes(b"durable object")

    shard = cas._path(ref).parent
    assert cas.verify(ref)
    assert events == [
        ("mkdir", shard),
        ("directory_fsync", cas.objects),
        "replace",
        ("directory_fsync", shard),
    ]


def test_executor_replay_does_not_repeat_completed_activity(tmp_path):
    calls = {"n": 0}
    def inc(payload):
        calls["n"] += 1
        return {"count": calls["n"]}
    def wf(ctx, inp):
        a = ctx.execute_activity("inc", {}, idempotency_key="a")
        b = ctx.execute_activity("inc", {}, idempotency_key="b")
        return [a, b]
    ex = DurableExecutor(tmp_path, {"inc": inc}, crash_after=1)
    r1 = ex.run("e1", wf, {})
    assert r1.status == "CRASHED" and calls["n"] == 1
    ex2 = DurableExecutor(tmp_path, {"inc": inc})
    r2 = ex2.run("e1", wf, {})
    assert r2.status == "COMPLETED"
    assert calls["n"] == 2  # первая активность взята из журнала, а не выполнена заново
    v = ex2.verify("e1")
    assert v["verified"] and v["digest"] == r2.digest


def test_pick_over_durable_executor_with_signal(tmp_path):
    calls = []
    reg = {
        "llm.plan": lambda q: {"query": q},
        "web.search": lambda q: ["a", "b"],
        "docs.top": lambda items, n: items[:n],
        "llm.write": lambda q, found: {"to": "x@y.z", "text": "hi"},
        "mail.send": lambda to, text: calls.append("mail") or {"id": "m1"},
    }
    src = open("examples/agent.pick", encoding="utf-8").read()
    r1, ex = run_pick(src, reg, {"q": "отчёт"}, tmp_path, "agent-1")
    assert r1.status == "WAITING" and r1.waiting_for == "approval"
    ex.deliver_signal("agent-1", "approval", True)
    r2, ex2 = run_pick(src, reg, {"q": "отчёт"}, tmp_path, "agent-1")
    assert r2.status == "COMPLETED" and calls == ["mail"]
    assert ex2.verify("agent-1")["verified"]


def test_cas_rejects_path_traversal_references(tmp_path):
    cas = ContentAddressedStore(tmp_path / "cas")
    outside = tmp_path / "outside"
    outside.write_text("must not be read", encoding="utf-8")

    # Before validation, this value would resolve from objects/ to tmp_path/outside.
    with pytest.raises(ValueError, match="invalid CAS reference"):
        cas.get_bytes("../../outside")
    assert outside.read_text(encoding="utf-8") == "must not be read"
    assert cas.has("../../outside") is False
    assert cas.verify("../../outside") is False


@pytest.mark.parametrize("ref", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, None, 123])
def test_cas_rejects_malformed_references(tmp_path, ref):
    cas = ContentAddressedStore(tmp_path / "cas")
    with pytest.raises(ValueError, match="invalid CAS reference"):
        cas._path(ref)
    assert cas.has(ref) is False
    assert cas.verify(ref) is False


def test_cas_recovery_removes_only_old_temporary_files(tmp_path):
    import os
    import time

    cas = ContentAddressedStore(tmp_path / "cas")
    ref = cas.put_text("committed object")
    shard = cas._path(ref).parent
    stale = shard / ".cas-abandoned.tmp"
    fresh = shard / ".cas-active.tmp"
    unrelated = shard / "notes.tmp"
    stale.write_bytes(b"orphan")
    fresh.write_bytes(b"active writer")
    unrelated.write_bytes(b"keep")
    old = time.time() - 600
    os.utime(stale, (old, old))

    result = cas.recover_temporary_files(min_age=300)

    assert result == {"removed_temporary_files": 1, "removed_bytes": 6}
    assert not stale.exists()
    assert fresh.read_bytes() == b"active writer"
    assert unrelated.read_bytes() == b"keep"
    assert cas.get_bytes(ref) == b"committed object"
    assert cas.verify(ref)


def test_cas_recovery_does_not_follow_or_remove_symlinks(tmp_path):
    import os

    cas = ContentAddressedStore(tmp_path / "cas")
    shard = cas.objects / "aa"
    shard.mkdir()
    target = tmp_path / "outside.tmp"
    target.write_text("keep", encoding="utf-8")
    link = shard / ".cas-link.tmp"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    result = cas.recover_temporary_files(min_age=0)
    assert result == {"removed_temporary_files": 0, "removed_bytes": 0}
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("age", [-1, float("nan"), float("inf"), True, "300"])
def test_cas_recovery_rejects_invalid_age(tmp_path, age):
    cas = ContentAddressedStore(tmp_path / "cas")
    with pytest.raises(ValueError, match="finite non-negative number"):
        cas.recover_temporary_files(min_age=age)

