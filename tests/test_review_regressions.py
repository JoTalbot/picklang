"""Регрессии по итогам внешнего аудита (19.10.2026).

Каждый тест воспроизводит найденный дефект до фикса и фиксирует инвариант после.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from durable_weave.pickrun import run_pick
from picklang.interpreter import run_program
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
    with pytest.raises(Exception):
        run_program(PAYMENT, _declining_bank(calls), {"id": "o1", "amt": 500}, prefer_aot=False)
    assert calls == ["reserve", "charge", "refund", "release"], calls


def test_guard_failure_rolls_back_saga_in_durable_runtime(tmp_path):
    calls = []
    result, _ = run_pick(PAYMENT, _declining_bank(calls), {"id": "o1", "amt": 500}, tmp_path, "pay-1")
    assert result.status == "FAILED"
    assert calls == ["reserve", "charge", "refund", "release"], calls


def test_interpreter_and_aot_agree_on_guard_rollback():
    interp, aot = [], []
    with pytest.raises(Exception):
        run_program(PAYMENT, _declining_bank(interp), {"id": "o1", "amt": 500}, prefer_aot=False)
    with pytest.raises(Exception):
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
    with pytest.raises(Exception):
        run_program(src, registry, {}, prefer_aot=False)
    assert calls == ["hold", "free"], calls
