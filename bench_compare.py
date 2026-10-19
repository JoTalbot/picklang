#!/usr/bin/env python3
"""Конкурентное сравнение на одной задаче: поиск -> top(2) -> форматирование.

Участники: чистый Python (потолок скорости), PICK интерпретатор, PICK авто (AOT),
LangGraph (StateGraph из 3 узлов), наш durable-исполнитель на SQLite
(единственный в списке, кто платит за краш-устойчивость, — это честно подписано).

Запуск: python3 bench_compare.py [--write]   (нужен langgraph для своей строки)
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from durable_weave import DurableExecutor                    # noqa: E402
from picklang import run_program                             # noqa: E402
from picklang.stdlib import with_stdlib                      # noqa: E402

DATA = [{"title": f"doc{i}", "score": i} for i in range(20)]
REG = with_stdlib({"docs.search": lambda q: DATA})
PICK_SRC = "> t(q)\nfound = docs.search(q) >> top(2)\nout found\n"


def bench_us(fn, n, warmup=20):
    for _ in range(warmup):
        fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1e6


def main():
    rows = []
    plain = bench_us(lambda: DATA[:2], 5000)
    rows.append(("Чистый Python (потолок)", plain, "—"))

    interp = bench_us(lambda: run_program(PICK_SRC, REG, {"q": "x"}, prefer_aot=False), 3000)
    rows.append(("Pick Интерпретатор", interp, f"{interp / plain:.0f}x от потолка"))

    auto = bench_us(lambda: run_program(PICK_SRC, REG, {"q": "x"}), 3000)
    rows.append(("Pick Авто (AOT)", auto, f"{auto / plain:.1f}x от потолка, {interp / auto:.1f}x быстрее интерпретатора"))

    try:
        from typing import TypedDict
        from langgraph.graph import END, START, StateGraph

        class S(TypedDict, total=False):
            q: str
            found: list

        g = StateGraph(S)
        g.add_node("search", lambda s: {"found": DATA})
        g.add_node("top", lambda s: {"found": s["found"][:2]})
        g.add_node("fmt", lambda s: {"found": s["found"]})
        g.add_edge(START, "search")
        g.add_edge("search", "top")
        g.add_edge("top", "fmt")
        g.add_edge("fmt", END)
        app = g.compile()
        lg = bench_us(lambda: app.invoke({"q": "x"}), 500)
        rows.append(("LangGraph (3 узла, без чекпоинтера)", lg, f"{lg / plain:.0f}x от потолка"))
    except ImportError:
        rows.append(("LangGraph", None, "не установлен: pip install langgraph"))

    with tempfile.TemporaryDirectory() as d:
        counter = {"i": 0}
        acts = {"search": lambda p: DATA, "top": lambda p: p["items"][:2], "fmt": lambda p: p["items"]}
        ex = DurableExecutor(d, acts, storage="sqlite", snapshot_every=1000)

        def wf(ctx, inp):
            a = ctx.execute_activity("search", {}, idempotency_key="s")
            b = ctx.execute_activity("top", {"items": a}, idempotency_key="t")
            return ctx.execute_activity("fmt", {"items": b}, idempotency_key="f")

        def one():
            counter["i"] += 1
            r = ex.run(f"cmp{counter['i']}", wf, {})
            assert r.status == "COMPLETED"
        dur = bench_us(one, 100, warmup=3)
        rows.append(("DurableWeave (SQLite, журнал+хэш-цепочка+CAS)", dur,
                     f"{dur / plain:.0f}x от потолка — цена краш-устойчивости и аудита"))

    print(f"{'Участник':52} {'мкс/запуск':>12}  Примечание")
    for name, us, note in rows:
        print(f"{name:52} {us:12.1f}  {note}" if us is not None else f"{name:52} {'—':>12}  {note}")
    if "--write" in sys.argv:
        lines = ["# Конкурентное сравнение (автогенерация: bench_compare.py --write)", "",
                 "Одна задача: поиск -> top(2) -> форматирование. Локальная машина, прогрев включён.", "",
                 "| Участник | мкс/запуск | Примечание |", "|---|---:|---|"]
        for name, us, note in rows:
            lines.append(f"| {name} | {us:.1f} | {note} |" if us is not None else f"| {name} | — | {note} |")
        lines += ["", "Честные оговорки: LangGraph меряется без чекпоинтера (его durable-режим "
                      "дороже); DurableWeave — единственный участник, пишущий журнал с хэш-цепочкой "
                      "и CAS на каждый шаг, поэтому его цифра — цена гарантий, а не оверхед ради оверхеда. "
                      "Чистый Python не даёт ни replay, ни аудита, ни лимитов — это потолок, а не конкурент по функциям."]
        Path("docs/COMPARE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/COMPARE.md обновлён")


if __name__ == "__main__":
    main()
