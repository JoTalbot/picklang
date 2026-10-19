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
