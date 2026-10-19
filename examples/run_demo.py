#!/usr/bin/env python3
"""Демо без внешних сервисов: сага оплаты (успех и откат) + агент с одобрением человека."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from picklang import run_program  # noqa: E402

PAYMENT = Path("examples/payment.pick").read_text(encoding="utf-8")
AGENT = Path("examples/agent.pick").read_text(encoding="utf-8")


def payment_registry(calls, fail=False):
    return {
        "stock.reserve": lambda id: calls.append("reserve") or {"id": id},
        "stock.release": lambda id: calls.append("release"),
        "bank.charge": lambda id, amt: calls.append("charge") or {"ok": True, "tx": "tx1"},
        "bank.refund": lambda tx: calls.append("refund"),
        "post.book": lambda id: (_ for _ in ()).throw(RuntimeError("нет доставки")) if fail else {"track": "tr1"},
        "post.cancel": lambda track: calls.append("cancel"),
        "mail.send": lambda *a: calls.append("mail") or {"id": "m1"},
    }


def main():
    calls = []
    out, *_ = run_program(PAYMENT, payment_registry(calls), {"id": 7, "amt": 100})
    print("сага OK:", out, "| вызовы:", calls)

    calls = []
    try:
        run_program(PAYMENT, payment_registry(calls, fail=True), {"id": 7, "amt": 100})
    except Exception as e:
        print("сага сорвалась:", e, "| вызовы:", calls)

    reg = {
        "llm.plan": lambda q: {"query": q},
        "web.search": lambda q: ["a", "b", "c"],
        "docs.top": lambda items, n: items[:n],
        "llm.write": lambda q, found: {"to": "x@y.z", "text": "hi"},
        "mail.send": lambda to, text: {"id": "m9"},
    }
    out, *_ = run_program(AGENT, reg, {"q": "отчёт"}, signal_provider=lambda s: True)
    print("агент OK:", out)


if __name__ == "__main__":
    main()
