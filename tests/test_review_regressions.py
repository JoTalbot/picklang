"""Регрессии по итогам внешнего аудита (19.10.2026).

Каждый тест воспроизводит найденный дефект до фикса и фиксирует инвариант после.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from durable_weave.pickrun import run_pick
from picklang.interpreter import PickRuntimeError, TaskNotDone, run_program
from picklang.stdlib import with_stdlib

PAYMENT = (Path(__file__).resolve().parent.parent / "examples/payment.pick").read_text(encoding="utf-8")


def _declining_bank(calls: list[str]):
    def note(name):
        calls.append(name)

    return with_stdlib({
        "stock.reserve": lambda id: (note("reserve"), {"ok": True})[1],
        "stock.release": lambda id: note("release"),
        "bank.charge": lambda id, amt: (note("charge"), {"ok": False, "tx": "TX1"})[1],
        "bank.refund": lambda tx: note("refund"),
        "post.book": lambda id: (note("book"), {"track": "TR1"})[1],
        "post.cancel": lambda t: note("cancel"),
        "mail.send": lambda id, text: {"id": "m1"},
    })


# --- 1. Компенсации при бизнес-сбое (страж / done when) -------------------

def test_guard_failure_rolls_back_saga_in_interpreter():
    calls = []
    with pytest.raises(PickRuntimeError):
        run_program(PAYMENT, _declining_bank(calls), {"id": "o1", "amt": 500}, prefer_aot=False)
    assert calls == ["reserve", "charge", "refund", "release"], calls


def test_guard_failure_rolls_back_saga_in_durable_runtime(tmp_path):
    calls = []
    result, _ = run_pick(PAYMENT, _declining_bank(calls), {"id": "o1", "amt": 500}, tmp_path, "pay-1")
    assert result.status == "FAILED"
    assert calls == ["reserve", "charge", "refund", "release"], calls


def test_interpreter_and_aot_agree_on_guard_rollback():
    interp, aot = [], []
    with pytest.raises(PickRuntimeError):
        run_program(PAYMENT, _declining_bank(interp), {"id": "o1", "amt": 500}, prefer_aot=False)
    with pytest.raises(PickRuntimeError):
        run_program(PAYMENT, _declining_bank(aot), {"id": "o1", "amt": 500}, prefer_aot=True)
    assert interp == aot, (interp, aot)


def test_done_when_failure_rolls_back_saga():
    calls = []
    src = ("> t()\n"
           "a = hold() ! free(a)\n"
           "out a\n"
           "done when: a.ok == 1\n")
    registry = with_stdlib({
        "hold": lambda: (calls.append("hold"), {"ok": 0})[1],
        "free": lambda a: calls.append("free"),
    })
    with pytest.raises(TaskNotDone):
        run_program(src, registry, {}, prefer_aot=False)
    assert calls == ["hold", "free"], calls


# --- 2. Идемпотентность: ключ должен быть уникален на шаг ------------------

def test_idempotency_key_is_unique_per_step(tmp_path):
    """Контракт «дедуплицируй по _idempotency_key» не должен схлопывать разные вызовы."""
    from durable_weave.executor import DurableExecutor

    seen: dict[str, int] = {}
    requested: list = []

    def charge(payload):
        requested.append(payload.get("_requested_key"))
        key = payload["_idempotency_key"]
        if key not in seen:
            seen[key] = payload["args"][0]
        return {"tx": f"tx-{seen[key]}"}

    executor = DurableExecutor(tmp_path, {"charge": charge})

    def wf(ctx, inp):
        a = ctx.execute_activity("charge", {"args": [10]}, idempotency_key="charge")
        b = ctx.execute_activity("charge", {"args": [20]}, idempotency_key="charge")
        return {"x": a["tx"], "y": b["tx"]}

    result = executor.run("e1", wf, {})
    assert result.output == {"x": "tx-10", "y": "tx-20"}
    assert requested == ["charge", "charge"]  # ключ вызывающего виден отдельно

    # Replay того же журнала: обработчик не вызывается повторно, результат стабилен.
    replay = DurableExecutor(tmp_path, {"charge": charge}).run("e1", wf, {})
    assert replay.output == result.output
    assert len(seen) == 2 and requested == ["charge", "charge"]


def test_async_idempotency_key_is_unique_per_call(tmp_path):
    import asyncio

    from durable_weave.async_executor import AsyncDurableExecutor

    seen: dict[str, int] = {}

    async def handler(payload):
        key = payload["_idempotency_key"]
        if key not in seen:
            seen[key] = payload["v"]
        return seen[key]

    async def main():
        executor = AsyncDurableExecutor(tmp_path, {"a": handler})
        executor._load("e1")
        first = await executor.call("a", {"v": 1}, "a")
        second = await executor.call("a", {"v": 2}, "a")
        assert (first, second) == (1, 2)

    asyncio.run(main())


# --- 3. Диагностики несут level; live-валидатор реально отбрасывает ----------

def test_check_diagnostics_carry_level():
    from picklang.check import check_effects, check_source

    registry = {"bank.charge": lambda u, a: {}}
    errors = check_source("> t(u)\nx = nope.fn(u)\nout x\n", registry)
    assert errors and all(d["level"] == "error" for d in errors)
    assert check_source("> t(u)\nx = bank.charge(u, 1)\nout x\n", registry) == []
    warnings = check_effects("> t(u)\nx = bank.charge(u, 1) @fx money\nout x\n")
    assert warnings and all(d["level"] == "warning" for d in warnings)


def test_live_validator_rejects_unknown_function_before_execution():
    """make_validator заявлен как «парсинг + check + исполнение»: check должен работать."""
    from eval_live_cascade import TASKS, make_validator

    task = next(t for t in TASKS if t["name"] == "charge_tx")
    calls: list[int] = []
    task = {**task, "registry": {"bank.charge": lambda u, a: calls.append(1) or {"ok": True, "tx": "t1"}}}
    validator = make_validator(task)

    assert validator("> t(u)\np = bank.charg(u, 5)\nout p.tx\n") is False
    assert calls == [], "программа не должна исполняться после статической ошибки"
    assert validator("> t(u)\np = bank.charge(u, 5)\nout p.tx\n") is True


# --- 4. Промпт не должен учить обратной семантике стража; CLI без traceback --

def test_grammar_prompt_matches_guard_semantics():
    from eval_live_cascade import GRAMMAR
    from picklang.interpreter import run_program

    registry = {"f": lambda x: x}
    src = "> t(x)\n? x -> fail \"bad\"\nout x\n"
    assert run_program(src, registry, {"x": True}, prefer_aot=False)[0] is True
    with pytest.raises(PickRuntimeError):
        run_program(src, registry, {"x": False}, prefer_aot=False)
    # страж падает, когда условие ЛОЖНО: промпт не должен утверждать обратное
    assert "если условие истинно" not in GRAMMAR
    assert "unless" in GRAMMAR.lower() or "ложно" in GRAMMAR


def test_cli_reports_syntax_error_without_traceback(tmp_path):
    import subprocess
    import sys

    bad = tmp_path / "bad.pick"
    bad.write_text('> t(u)\n? u.ok fail "x"\nout u\n', encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "picklang.cli", "check", str(bad)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "line 2" in proc.stderr


# --- 5. Документированные литералы true/false/null -------------------------

def test_boolean_and_null_literals_parse_as_data():
    """parser.py обещает true/false/null как данные, а отдавал их переменными."""
    from picklang.interpreter import run_program
    from picklang.parser import parse

    assert parse("> t()\nout true\n")[-1].expr == ("lit", True)
    for expression, expected in (("true", True), ("false", False), ("null", None),
                                 ("True", True), ("False", False), ("None", None)):
        out = run_program(f"> t()\nout {expression}\n", {}, {})[0]
        assert out is expected, (expression, out)

    # done when с литералом и страж-сравнение теперь работают
    calls = []
    src = "> t()\na = hold() ! free(a)\nout a\ndone when: a.ok == True\n"
    registry = {"hold": lambda: (calls.append("hold"), {"ok": True})[1], "free": lambda a: calls.append("free")}
    assert run_program(src, registry, {}, prefer_aot=False)[0] == {"ok": True}
    assert calls == ["hold"]
