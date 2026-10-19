#!/usr/bin/env python3
"""Нагрузка журнала: 100k событий — запись, verify, снимок, компакция, resume.

Запуск: python3 bench_load_journal.py [--write]
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from durable_weave import DurableExecutor          # noqa: E402
from durable_weave.sqlite_backend import SQLiteEventLog  # noqa: E402

N = 100_000

def main():
    rows = []
    with tempfile.TemporaryDirectory() as d:
        log = SQLiteEventLog(Path(d) / "big.db")
        t0 = time.perf_counter()
        B = 5000
        for start in range(0, N, B):
            log.append_many([("ActivityCompleted", {"step": f"s{i}", "n": i}) for i in range(start, min(start + B, N))])
        rows.append((f"Запись {N} событий пачками по {B}", f"{time.perf_counter() - t0:.1f} с ({N / (time.perf_counter() - t0):,.0f} соб/с)"))
        size = (Path(d) / "big.db").stat().st_size
        rows.append(("Размер БД", f"{size / 1e6:.1f} МБ"))
        t0 = time.perf_counter(); ok, _ = log.verify_chain()
        rows.append(("Полный verify_chain", f"{time.perf_counter() - t0:.1f} с, ok={ok}"))
        t0 = time.perf_counter(); ok2, _ = log.verify_sampled(every=100)
        rows.append(("Выборочный verify (каждое 100-е)", f"{(time.perf_counter() - t0) * 1000:.0f} мс, ok={ok2}"))
        t0 = time.perf_counter()
        log2 = SQLiteEventLog(Path(d) / "big.db")
        rows.append((f"Загрузка {N} событий из БД", f"{time.perf_counter() - t0:.1f} с"))

    # снимок + компакция на длинном исполнении
    with tempfile.TemporaryDirectory() as d:
        steps = 300
        def wf(ctx, inp):
            return [ctx.execute_activity("inc", {}, idempotency_key=f"k{i}") for i in range(steps)]
        ex = DurableExecutor(d, {"inc": lambda p: {"n": 1}}, storage="sqlite", snapshot_every=50)
        ex.run("long", wf, {})
        t0 = time.perf_counter(); info = ex.log.compact(); dt = time.perf_counter() - t0
        ex2 = DurableExecutor(d, {"inc": lambda p: {"n": 1}}, storage="sqlite", snapshot_every=50)
        t0 = time.perf_counter(); r = ex2.run("long", wf, {}); resume = time.perf_counter() - t0
        rows.append((f"Компакция после {steps} шагов", f"{info}, {dt * 1000:.0f} мс"))
        rows.append(("Resume после компакции", f"{resume * 1000:.0f} мс, status={r.status}, verify={ex2.verify('long')['verified']}"))
    for name, v in rows:
        print(f"{name:42} {v}")
    if "--write" in sys.argv:
        lines = ["# Нагрузка журнала (автогенерация: bench_load_journal.py --write)", "",
                 "| Замер | Значение |", "|---|---|"] + [f"| {n} | {v} |" for n, v in rows]
        Path("docs/LOAD.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/LOAD.md обновлён")

if __name__ == "__main__":
    main()
