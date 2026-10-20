"""Канонические решения и валидаторы живого набора задач (без API)."""
from __future__ import annotations

import pytest

from eval_live_cascade import GRAMMAR, TASKS, make_validator

CANONICAL_PICK = {
    "charge_tx": '> t(u)\np = bank.charge(u, 5)\nout p.tx\n',
    "search_top1": '> t(q)\nr = web.search(q) >> top(1)\nout r\n',
    "double_value": '> t(i)\nr = dbl(i)\nout r\n',
    "fields_a": '> t(x)\nr = d.get(x) >> fields("a")\nout r\n',
    "count_results": '> t(q)\nn = web.search(q) >> count()\nout n\n',
    "multi_step_subscribe": '> t(u)\ns = db.create_sub(u)\nm = mail.send(u, s.id)\nout {sub: s.id, message: m.id}\n',
    "guard_pass": '> t(u, amt)\np = bank.charge(u, amt)\n? p.ok -> fail "declined"\nout p.tx\n',
    "guard_decline": '> t(u, amt)\np = bank.charge(u, amt)\n? p.ok -> fail "declined"\nout p.tx\n',
    "saga_rollback": '> t(u, amt)\np = bank.charge(u, amt) ! bank.refund(p.tx)\n? p.ok -> fail "declined"\nout p.tx\n',
    "wait_approval": '> t(user)\n@wait approval\n? approval -> fail "rejected"\nm = mail.send(user, "ok")\nout m.id\n',
    "parallel_two": '> t(x)\na = left(x) || b = right(x)\nout {a: a, b: b}\n',
    "budget_stop": '> t()\n!budget steps<1\ntick()\ntick()\nout 1\n',
}

GUARD_TASKS = {"guard_pass", "guard_decline", "saga_rollback", "wait_approval", "parallel_two", "budget_stop"}


def test_every_task_has_a_canonical_solution():
    names = {task["name"] for task in TASKS}
    assert names == set(CANONICAL_PICK), "новые задачи должны получить каноническое решение и валидатор"


@pytest.mark.parametrize("name", sorted(CANONICAL_PICK))
def test_canonical_solution_passes_its_own_validator(name):
    task = next(item for item in TASKS if item["name"] == name)
    assert make_validator(task)(CANONICAL_PICK[name]) is True


def test_guard_decline_rejects_program_without_guard():
    task = next(item for item in TASKS if item["name"] == "guard_decline")
    assert make_validator(task)("> t(u, amt)\np = bank.charge(u, amt)\nout p.tx\n") is False


def test_saga_rollback_requires_a_compensation():
    task = next(item for item in TASKS if item["name"] == "saga_rollback")
    without_undo = '> t(u, amt)\np = bank.charge(u, amt)\n? p.ok -> fail "declined"\nout p.tx\n'
    assert make_validator(task)(without_undo) is False


def test_wait_task_rejects_missing_wait():
    task = next(item for item in TASKS if item["name"] == "wait_approval")
    assert make_validator(task)('> t(user)\nm = mail.send(user, "ok")\nout m.id\n') is False


def test_grammar_stays_compact_and_states_guard_semantics():
    """Грамматика уходит в каждый запрос: держим бюджет и верную семантику стража."""
    tiktoken = pytest.importorskip("tiktoken")
    tokens = len(tiktoken.get_encoding("cl100k_base").encode(GRAMMAR))
    assert tokens <= 240, f"GRAMMAR разрослась до {tokens} токенов"
    assert "если условие истинно" not in GRAMMAR
    assert "condition is false" in GRAMMAR
    assert "Guard+saga example:" in GRAMMAR
    assert "out p.tx" in GRAMMAR


def test_failed_model_response_is_logged_with_a_bounded_preview(monkeypatch):
    import picklang.cascade as cascade_module
    from picklang.cascade import ModelCascade

    monkeypatch.setattr(cascade_module, "chat", lambda *args, **kwargs: ("x" * 500, {"completion_tokens": 500}))
    cascade = ModelCascade([["fake-model"]])
    with pytest.raises(RuntimeError, match="каскад исчерпан"):
        cascade.run("unused", [], lambda _text: False)
    assert cascade.log[0]["response_preview"] == "x" * 240
