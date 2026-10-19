import pytest

from picklang import parse, run_program
from picklang.diff import PickPatchError, apply_patch
from picklang.interpreter import PickRuntimeError, WaitingSignal
from picklang.ir import compile_ir, program_digest

PAYMENT = open("examples/payment.pick", encoding="utf-8").read()
AGENT = open("examples/agent.pick", encoding="utf-8").read()


def registry(calls, fail_delivery=False):
    return {
        "stock.reserve": lambda id: calls.append("reserve") or {"id": id},
        "stock.release": lambda id: calls.append("release"),
        "bank.charge": lambda id, amt: calls.append("charge") or {"ok": True, "tx": "tx1"},
        "bank.refund": lambda tx: calls.append("refund"),
        "post.book": lambda id: (_ for _ in ()).throw(RuntimeError("нет доставки")) if fail_delivery else {"track": "tr1"},
        "post.cancel": lambda track: calls.append("cancel"),
        "mail.send": lambda *a: calls.append("mail") or {"id": "m1"},
    }


def test_payment_success():
    calls = []
    out, env, trace = run_program(PAYMENT, registry(calls), {"id": 7, "amt": 100})
    assert out == {"order": 7, "tx": "tx1", "track": "tr1"}
    assert calls == ["reserve", "charge", "mail"]


def test_payment_compensations_lifo_on_failure():
    calls = []
    with pytest.raises(PickRuntimeError) as e:
        run_program(PAYMENT, registry(calls, fail_delivery=True), {"id": 7, "amt": 100})
    # обратный порядок: сначала refund, потом release
    assert calls == ["reserve", "charge", "refund", "release"]
    assert "компенсаций выполнено: 2" in str(e.value)


def test_agent_waits_then_guard_rejects_then_accepts():
    reg = {
        "llm.plan": lambda q: {"query": q},
        "web.search": lambda q: ["a", "b", "c", "d"],
        "docs.top": lambda items, n: items[:n],
        "llm.write": lambda q, found: {"to": "x@y.z", "text": "hi"},
        "mail.send": lambda to, text: {"id": "m9"},
    }
    with pytest.raises(WaitingSignal):
        run_program(AGENT, reg, {"q": "отчёт"}, signal_provider=lambda s: None)
    with pytest.raises(PickRuntimeError):
        run_program(AGENT, reg, {"q": "отчёт"}, signal_provider=lambda s: False)
    out, env, trace = run_program(AGENT, reg, {"q": "отчёт"}, signal_provider=lambda s: True)
    assert out == {"to": "x@y.z", "id": "m9"}
    assert "web.search" in trace and "docs.top" in trace  # пайплайн >> отработал


def test_ir_digest_stable_and_patch():
    assert program_digest(PAYMENT) == program_digest(PAYMENT)
    ir = compile_ir(PAYMENT)
    assert ir["lang"] == "pick" and len(ir["steps"]) == 7
    patched = apply_patch(PAYMENT, '~4: ? p.ok -> fail "стоп"\n', expected_digest=program_digest(PAYMENT))
    assert program_digest(patched) != program_digest(PAYMENT)
    with pytest.raises(PickPatchError):
        apply_patch(PAYMENT, '-1\n', expected_digest="0" * 64)


def test_syntax_error_is_structured():
    from picklang import PickSyntaxError
    with pytest.raises(PickSyntaxError) as e:
        parse("x = ")
    assert e.value.line == 1
