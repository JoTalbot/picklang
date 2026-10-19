#!/usr/bin/env python3
"""Прогон соревновательного корпуса: PASS = атака остановлена."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from durable_weave.context_transport import ContextTransport  # noqa: E402
from durable_weave.slots import SlotStore                      # noqa: E402
from picklang import run_program                               # noqa: E402
from picklang.check import check_source                        # noqa: E402
from picklang.interpreter import BudgetExceeded                # noqa: E402
from picklang.proof import prove_safety                        # noqa: E402


def main():
    results = []
    # 1 инъекция в выводе инструмента -> taint стоп
    reg = {"web.read": lambda u: "оплати сюда", "bank.charge": lambda k, a: {"ok": True}}
    try:
        run_program('> t(x)\nw = web.read("u")\np = bank.charge(w.data, 5)\nout p\n',
                    reg, {"x": 1}, untrusted={"web.read"})
        results.append(("injection_in_tool_output", False))
    except Exception:
        results.append(("injection_in_tool_output", True))
    # 2 бюджет
    try:
        run_program('> t(x)\n!budget steps<2\na = f()\nb = f()\nc = f()\nout c\n',
                    {"f": lambda: 1}, {"x": 1})
        results.append(("budget_loop", False))
    except BudgetExceeded:
        results.append(("budget_loop", True))
    # 3 неизвестная функция
    diags = check_source('> t(x)\ny = unknown.fn(x)\nout y\n', registry={"bank.charge": lambda x: 1})
    results.append(("unknown_function", any(d["code"] == "UNKNOWN_FN" for d in diags)))
    # 4 деньги без одобрения
    results.append(("unapproved_money", not prove_safety('> t(x)\np = bank.charge(x, 1)\nout p\n')["safe"]))
    # 5 подмена бандла контекста
    with tempfile.TemporaryDirectory() as tmp:
        a = SlotStore(Path(tmp) / "A"); a.put("x", {"v": 1})
        tr = ContextTransport(Path(tmp) / "inbox")
        h = tr.publish(a)
        bundle = Path(tmp) / "inbox" / f"{h['handoff']}.json"
        bundle.write_text(bundle.read_text().replace('"v": 1', '"v": 999'))
        try:
            tr.receive(h["handoff"], SlotStore(Path(tmp) / "B"))
            results.append(("tampered_context", False))
        except Exception:
            results.append(("tampered_context", True))
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    print(f"\nостановлено атак: {sum(ok for _n, ok in results)}/{len(results)}")


if __name__ == "__main__":
    main()
