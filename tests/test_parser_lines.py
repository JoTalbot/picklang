"""Вложенные шаги наследуют номер строки исходника, а не фрагмента.

Регрессия: тело `for` и `else`-шаг у `@wait` парсятся отдельным вызовом
parse() и получали line=1; любая диагностика по таким шагам указывала
на первую строку программы.
"""
from picklang.parser import parse


def test_loop_body_inherits_source_line():
    steps = parse("> t(xs)\nfor i in xs: p = bank.charge(i, 1)\nout p\n")
    loop = next(s for s in steps if s.kind == "loop")
    assert loop.line == 2
    assert loop.body.line == 2


def test_wait_else_step_inherits_source_line():
    steps = parse("> t(x)\n@wait approval :5s else notify(x)\nout x\n")
    wait = next(s for s in steps if s.kind == "wait")
    assert wait.else_step is not None
    assert wait.else_step.line == 2
