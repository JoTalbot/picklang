"""Структурный контроль check_ir: ветви ||, циклы for и пайплайны."""
from __future__ import annotations

from picklang.check import check_source


def test_parallel_branches_define_their_targets():
    parallel = '> t(x)\na = left(x) || b = right(x)\nout {a: a, b: b}\n'
    assert check_source(parallel, {"left": lambda x: x, "right": lambda x: x}) == []


def test_parallel_branch_expressions_are_still_checked():
    broken = '> t(x)\na = left(x) || b = right(z)\nout {a: a, b: b}\n'
    codes = {d["code"] for d in check_source(broken, {"left": lambda x: x, "right": lambda x: x})}
    assert "UNDEFINED_VAR" in codes


def test_loop_body_assigns_a_visible_target():
    loop = "> t(xs)\nfor i in xs: y = dbl(i)\nout y\n"
    assert check_source(loop, {"dbl": lambda i: i * 2}) == []


def test_done_when_conditions_are_scanned():
    broken = "> t()\na = f()\nout a\ndone when: missing == 1, a == 1\n"
    codes = {d["code"] for d in check_source(broken, {"f": lambda: 1})}
    assert "UNDEFINED_VAR" in codes


def test_pipeline_stages_get_the_injected_argument():
    """Результат левой части — первый аргумент правой: >> count() не ARITY-ошибка."""
    pipeline = "> t(q)\nn = web.search(q) >> count()\nout n\n"
    assert check_source(pipeline, {"web.search": lambda q: [], "count": lambda items: len(items)}) == []
    # а вот лишний аргумент у стадии всё ещё ловится: 1 объявленный + 1 инъекция > 1
    wrong = "> t(q)\nn = web.search(q) >> count(1)\nout n\n"
    assert any(d["code"] == "ARITY" for d in check_source(wrong, {"web.search": lambda q: [], "count": lambda items: 0}))
