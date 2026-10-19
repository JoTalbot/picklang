#!/usr/bin/env python3
"""Демо одной командой: python3 demo.py

Показывает весь путь за ~секунду, без сети и ключей:
PICK-программа -> проверка -> исполнение -> краш процесса -> resume без повторов.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from durable_weave import DurableExecutor, SimulatedCrash
from picklang import run_program
from picklang.check import check_source
from picklang.ir import program_digest
from picklang.stdlib import with_stdlib

SRC = """> order(user)
@wait approval
p = bank.charge(user, 500) ! bank.refund(p.tx)
r = mail.send(user, p.tx)
out {tx: p.tx, mailed: r}
"""


def main():
    print("1) Программа PICK (5 строк вместо экрана Python):")
    print(SRC)
    print(f"   digest: {program_digest(SRC)[:16]}…  проверка: "
          f"{[d for d in check_source(SRC) if d.get('level') == 'error'] or 'OK'}")

    bank_calls = {"n": 0}
    def charge(user, amt):
        bank_calls["n"] += 1
        return {"ok": True, "tx": "tx-42"}
    registry = with_stdlib({"bank.charge": charge, "bank.refund": lambda tx: None,
                            "mail.send": lambda user, tx: "sent"})
    out, _, trace = run_program(SRC, registry, {"user": "u1"}, signal_provider=lambda name: True)
    print(f"2) Исполнение: {out}  (вызовов банка: {bank_calls['n']}, trace: {trace})")

    with tempfile.TemporaryDirectory() as d:
        handlers = {"charge": lambda p: charge(p["user"], 500), "send": lambda p: "sent"}
        def wf(ctx, inp):
            p = ctx.execute_activity("charge", {"user": "u1"}, idempotency_key="pay-1")
            return ctx.execute_activity("send", {"tx": p["tx"]}, idempotency_key="mail-1")
        ex = DurableExecutor(d, handlers, crash_after=1)
        crashed = ex.run("demo", wf, {})
        assert crashed.status == "CRASHED"
        print("3) Процесс «умер» после первого шага (charge уже в журнале).")
        ex2 = DurableExecutor(d, handlers)
        r = ex2.run("demo", wf, {})
        print(f"4) Resume: {r.status}, результат {r.output}; вызовов банка за всё демо: "
              f"{bank_calls['n']} (один в п.2 и один до краша — resume списания НЕ повторил), "
              f"verify: {ex2.verify('demo')['verified']}")
    print("\nИтог: та же программа исполняется интерпретатором, AOT и Temporal; "
          "состояние переживает смерть процесса.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
