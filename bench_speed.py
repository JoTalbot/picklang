#!/usr/bin/env python3
"""Единый скоростной бенчмарк: интерпретатор vs AOT, batch-цикл, снимки, параллелизм.

Все цифры — локальные замеры этой машины, прогрев включён там, где меряется
установившийся режим. `--write` -> docs/SPEED.md.
Запуск: python3 bench_speed.py [--write]
"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from durable_weave import DurableExecutor                    # noqa: E402
from picklang import run_program                             # noqa: E402
from picklang.aot import run_compiled                        # noqa: E402


def bench(fn, n):
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1e6  # мкс


def main():
    rows = []
    reg = {"f": lambda x: x + 1}
    src = "> t(x)\ny = f(x)\nout y\n"
    interp = bench(lambda: run_program(src, reg, {"x": 1}), 3000)
    aot = bench(lambda: run_compiled(src, reg, {"x": 1}), 3000)
    rows.append(("Один шаг: интерпретатор", f"{interp:.1f} мкс"))
    rows.append(("Один шаг: AOT (скомпилировано)", f"{aot:.1f} мкс"))
    if aot:
        rows.append(("AOT против интерпретатора", f"{interp / aot:.1f}x быстрее"))

    # batch-цикл против поэлементного
    items = list(range(200))
    per_item = {"ship": lambda i: i}
    t0 = time.perf_counter()
    run_program('> t(xs)\nfor i in xs: y = ship(i)\nout y\n', per_item, {"xs": items})
    t_item = time.perf_counter() - t0
    batched = {"ship": lambda i: i, "ship.batch": lambda xs: xs}
    t0 = time.perf_counter()
    run_program('> t(xs)\nfor i in xs: y = ship(i)\nout y\n', batched, {"xs": items})
    t_batch = time.perf_counter() - t0
    rows.append(("Цикл 200 элементов: по одному", f"{t_item * 1000:.1f} мс"))
    rows.append(("Цикл 200 элементов: batch", f"{t_batch * 1000:.1f} мс"))

    # снимки: resume на длинном исполнении
    with tempfile.TemporaryDirectory() as d:
        calls = {"n": 0}
        def inc(p):
            calls["n"] += 1
            return calls["n"]
        def wf(ctx, inp):
            return [ctx.execute_activity("inc", {}, idempotency_key=f"k{i}") for i in range(60)]
        ex = DurableExecutor(d, {"inc": inc}, storage="sqlite", snapshot_every=10)
        ex.run("long", wf, {})
        ex2 = DurableExecutor(d, {"inc": inc}, storage="sqlite", snapshot_every=10)
        t0 = time.perf_counter()
        r = ex2.run("long", wf, {})
        rows.append(("Resume 60 шагов со снимками", f"{(time.perf_counter() - t0) * 1000:.1f} мс, снимок использован: {ex2.snapshot_used}, новых вызовов: {calls['n'] - 60}"))
        assert r.output == list(range(1, 61))

    for name, value in rows:
        print(f"{name:42} {value}")
    if "--write" in sys.argv:
        lines = ["# Скоростные замеры (автогенерация: bench_speed.py --write)", "",
                 "| Замер | Значение |", "|---|---|"]
        lines += [f"| {n} | {v} |" for n, v in rows]
        lines += ["", "Методика: локальная машина, прогрев перед замером интерпретатора/AOT; "
                      "для сетевых инструментов цифры будут определяться сетью, а не рантаймом."]
        Path("docs/SPEED.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/SPEED.md обновлён")


if __name__ == "__main__":
    main()
