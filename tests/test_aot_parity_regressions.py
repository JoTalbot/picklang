"""Регрессии дифференциального фаззинга AOT <-> интерпретатор.

До этих фиксов auto-AOT мог: падать KeyError на bare-вызове в теле цикла,
терять ретраи/компенсации тела цикла, превращать неопределённые переменные
в None, проверять done when в месте объявления, а не в конце программы,
и отдавать сырые исключения хоста вместо PickRuntimeError.
"""
from types import SimpleNamespace

import pytest

from picklang import run_program
from picklang.aot import generate_python, run_compiled
from picklang.interpreter import _aot_eligible
from picklang.ir import compile_ir, program_digest
from picklang.stdlib import with_stdlib


def both(source, registry_factory, inputs):
    results = []
    for prefer_aot in (False, True):
        calls = []
        try:
            out = run_program(source, registry_factory(calls), dict(inputs), prefer_aot=prefer_aot)
            results.append(("ok", out, calls))
        except BaseException as exc:  # сравниваем и поверхность ошибок
            results.append(("error", type(exc).__name__, str(exc), calls))
    assert results[0] == results[1], (source, results)
    return results[0]


def test_ir_preserves_retries_effects_and_loop_body_metadata():
    base = "> t(xs)\nfor i in xs: y = f(i)\nout y\n"
    retry = "> t(xs)\nfor i in xs: y = f(i) ^2\nout y\n"
    compensated = "> t(xs)\nfor i in xs: y = f(i) ! undo(y)\nout y\n"

    assert compile_ir(retry)["steps"][1]["body"]["retries"] == 2
    assert "compensation" in compile_ir(compensated)["steps"][1]["body"]
    assert program_digest(base) != program_digest(retry)
    assert program_digest(base) != program_digest(compensated)


def test_aot_loop_bare_call():
    def factory(calls):
        return {"tick": lambda x: calls.append(x) or x}

    result = both("> t(xs)\nfor i in xs: tick(i)\nout 7\n", factory, {"xs": [1, 2]})
    assert result[1][0] == 7
    assert result[2] == [1, 2]


def test_loop_retry_and_compensation_fall_back_to_interpreter():
    retry_src = "> t(xs)\nfor i in xs: y = flaky(i) ^2\nout y\n"
    comp_src = ("> t(xs)\nfor i in xs: p = bank.charge(i) ! bank.refund(p.tx)\n"
                "? 1 > 2 -> fail \"stop\"\nout p\n")

    for src in (retry_src, comp_src):
        assert _aot_eligible(src, None, None, ()) is False
        with pytest.raises(ValueError, match="AOT does not support"):
            generate_python(src)

    def retry_factory(calls):
        seen = set()

        def flaky(x):
            calls.append(x)
            if x not in seen:
                seen.add(x)
                raise ValueError("transient")
            return x * 10

        return {"flaky": flaky}

    assert both(retry_src, retry_factory, {"xs": [3]})[1][0] == 30

    def bank_factory(calls):
        return {
            "bank.charge": lambda x: calls.append(("charge", x)) or {"tx": x},
            "bank.refund": lambda tx: calls.append(("refund", tx)),
        }

    result = both(comp_src, bank_factory, {"xs": [4]})
    assert result[0] == "error"
    assert result[3] == [("charge", 4), ("refund", 4)]


@pytest.mark.parametrize("source,inputs", [
    ("> t()\nout missing\n", {}),
    ("> t(x)\nout 3\n", {}),
    ("> t(x)\nout x\n", {}),
    ("> t(xs)\nfor i in xs: y = dbl(i)\nout y\n", {"xs": []}),
])
def test_aot_preserves_definedness(source, inputs):
    result = both(source, lambda calls: {"dbl": lambda i: i * 2}, inputs)
    assert result[:3][0] == "error"
    assert result[1] == "PickRuntimeError"


def test_aot_empty_loop_does_not_invent_env_vars():
    def factory(calls):
        return {"dbl": lambda i: i * 2}

    _status, (_out, env, _trace), _calls = both(
        "> t(xs)\nfor i in xs: y = dbl(i)\nout xs\n", factory, {"xs": []})
    assert env == {"xs": []}


def test_done_when_is_checked_after_all_steps():
    def factory(calls):
        return {"inc": lambda x: x + 1}

    src = "> t(x)\ny = inc(x)\ndone when: y > 5\ny = inc(y)\nout y\n"
    assert both(src, factory, {"x": 4})[1][0] == 6


def test_done_when_failure_compensates_and_stays_task_not_done():
    def factory(calls):
        return {
            "bank.charge": lambda x: calls.append("charge") or {"tx": 1, "ok": True},
            "bank.refund": lambda tx: calls.append("refund"),
        }

    src = ("> t(x)\np = bank.charge(x) ! bank.refund(p.tx)\n"
           "out p\ndone when: p.ok == 0\n")
    result = both(src, factory, {"x": 1})
    assert result[1] == "TaskNotDone"
    assert result[3] == ["charge", "refund"]


def test_guard_reason_and_host_error_surface():
    guard = both(
        "> t(x)\n? x > 5 -> fail \"too small\"\nout x\n",
        lambda calls: {}, {"x": 1})
    assert guard[1:3] == ("PickRuntimeError", "guard failed: too small")

    def boom_factory(calls):
        def boom(x):
            calls.append(x)
            raise ValueError("host boom")
        return {"boom": boom}

    failed = both("> t(x)\ny = boom(x)\nout y\n", boom_factory, {"x": 1})
    assert failed[1] == "PickRuntimeError"
    assert "причина: host boom" in failed[2]


def test_object_fields_and_mixed_type_equality():
    def factory(calls):
        return {"obj": lambda x: SimpleNamespace(v=x + 5)}

    assert both("> t(x)\ny = obj(x)\nout y.v\n", factory, {"x": 2})[1][0] == 7
    assert both("> t(x)\nout x == 1\n", lambda calls: {}, {"x": "1"})[1][0] is False


def test_batch_loop_sets_loop_variable_in_env():
    def factory(calls):
        return {
            "ship": lambda i: i * 10,
            "ship.batch": lambda xs: [x * 10 for x in xs],
        }

    src = "> t(xs)\nfor i in xs: y = ship(i)\nout y\n"
    for xs, expected_i in (([1, 2, 3], 3), ([], None)):
        _status, (_out, env, _trace), _calls = both(src, factory, {"xs": xs})
        assert env["i"] == expected_i


def test_failed_compensation_neither_masks_error_nor_stops_rollback():
    def factory(calls):
        def bad_refund(tx):
            calls.append(("bad-refund", tx))
            raise RuntimeError("refund unavailable")

        return {
            "charge": lambda x: calls.append(("charge", x)) or {"tx": x},
            "refund": lambda tx: calls.append(("refund", tx)),
            "bad_refund": bad_refund,
        }

    src = ("> t(x)\n"
           "a = charge(1) ! refund(a.tx)\n"
           "b = charge(2) ! bad_refund(b.tx)\n"
           "? 1 > 2 -> fail \"stop\"\n"
           "out b\n")
    result = both(src, factory, {"x": 1})
    assert result[1:3] == ("PickRuntimeError", "guard failed: stop")
    assert result[3] == [("charge", 1), ("charge", 2), ("bad-refund", 2), ("refund", 1)]


def _ship_factory(calls):
    def ship(i, *rest):
        calls.append(("ship", i, rest))
        return (i, rest)

    def batch(xs, *rest):
        calls.append(("batch", list(xs), rest))
        return [("batch", list(xs), rest)]

    return {"ship": ship, "ship.batch": batch}


def test_batch_is_only_used_for_elementwise_loop_bodies():
    # ship(10) и ship(i, i) — не поэлементная форма: batch изменил бы смысл.
    for src in ("> t(xs)\nfor i in xs: y = ship(10)\nout y\n",
                "> t(xs)\nfor i in xs: y = ship(i, i)\nout y\n"):
        result = both(src, _ship_factory, {"xs": [1, 2], "i": 99})
        assert all(call[0] == "ship" for call in result[2])


def test_batch_materializes_generator_once():
    src = "> t(xs)\nfor i in xs: y = ship(i)\nout {y: y, i: i}\n"
    results = []
    for prefer_aot in (False, True):
        calls = []
        out, env, _trace = run_program(
            src, _ship_factory(calls), {"xs": (v for v in [1, 2])}, prefer_aot=prefer_aot)
        results.append((out, env["y"], env["i"], calls))
    assert results[0] == results[1]
    assert results[0][3] == [("batch", [1, 2], ())]


def test_unknown_function_error_matches():
    result = both("> t(x)\ny = nope(x)\nout y\n", lambda calls: {}, {"x": 1})
    assert result[1:3] == ("PickRuntimeError", "функция не зарегистрирована хостом: nope")


def test_custom_top_fields_are_not_bypassed_by_fusion():
    def factory(calls):
        return with_stdlib({
            "rowsfn": lambda x: [{"v": x}, {"v": x + 1}],
            "top": lambda items, n=3: list(items)[-int(n):],
            "fields": lambda item, *names: [{"custom": 1}],
        })

    src = "> t(x)\ny = rowsfn(x) >> top(1) >> fields(\"v\")\nout y\n"
    assert both(src, factory, {"x": 1})[1][0] == [{"custom": 1}]
    with pytest.raises(ValueError, match="requires stdlib top/fields"):
        run_compiled(src, factory([]), {"x": 1})


def test_done_condition_error_compensates_and_wraps():
    def factory(calls):
        def badcheck(x):
            calls.append("badcheck")
            raise ValueError("check boom")

        return {
            "inc": lambda x: x + 1,
            "badcheck": badcheck,
            "bank.charge": lambda x: calls.append("charge") or {"tx": x},
            "bank.refund": lambda tx: calls.append("refund"),
        }

    plain = both("> t(x)\ny = inc(x)\ndone when: badcheck(y)\nout y\n",
                 factory, {"x": 3})
    assert plain[1] == "PickRuntimeError"
    assert "причина: check boom" in plain[2]

    saga = both("> t(x)\np = bank.charge(x) ! bank.refund(p.tx)\n"
                "done when: badcheck(x)\nout p\n", factory, {"x": 3})
    assert saga[1] == "PickRuntimeError"
    assert saga[3] == ["charge", "badcheck", "refund"]


def test_wait_timeout_else_preserved_in_ir_and_aot_fails_closed():
    src = "> t(x)\n@wait approval :5s else notify(x)\nout x\n"
    step = compile_ir(src)["steps"][1]
    assert step["timeout"] == 5
    assert step["else_step"]["kind"] == "expr"
    with pytest.raises(ValueError, match="wait timeout/else"):
        generate_python(src)

    result = both(src, lambda calls: {"notify": lambda x: calls.append(x) or x}, {"x": 1})
    assert result[1][0] == 1
    assert result[2] == [1]
