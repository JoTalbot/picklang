#!/usr/bin/env python3
"""Прогон eval-корпуса: успешность, токены, вызовы. `--write` -> docs/EVAL.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench.eval_tasks import TASKS          # noqa: E402
from picklang import run_program            # noqa: E402
from picklang.result_guard import estimate_tokens  # noqa: E402
from picklang.stdlib import with_stdlib     # noqa: E402


def main():
    rows, passed = [], 0
    for t in TASKS:
        reg = with_stdlib(dict(t["registry"]))
        try:
            out, _env, trace = run_program(t["src"], reg, dict(t["inputs"]),
                                           signal_provider=lambda s: True)
            ok = out == t["expected"]
        except Exception as e:
            out, trace, ok = f"{type(e).__name__}: {e}", [], False
        passed += ok
        rows.append((t["name"], ok, estimate_tokens(t["src"]), len(trace)))
        print(f"{'PASS' if ok else 'FAIL'} {t['name']:18} токенов программы: {rows[-1][2]:>4} вызовов: {len(trace)}")
    print(f"\nуспешность: {passed}/{len(TASKS)}")
    if "--write" in sys.argv:
        lines = ["# Eval-корпус агентных задач (автогенерация: eval_harness.py --write)", "",
                 "| Задача | Итог | Токенов программы | Вызовов |", "|---|---|---:|---:|"]
        lines += [f"| {n} | {'PASS' if ok else 'FAIL'} | {tok} | {calls} |" for n, ok, tok, calls in rows]
        lines += ["", f"**Успешность на моках: {passed}/{len(TASKS)}.** Это нижняя планка качества: "
                      "программы написаны человеком; живой вариант — те же задачи через LLM (live_cycle_openrouter.py)."]
        Path("docs/EVAL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/EVAL.md обновлён")


if __name__ == "__main__":
    main()
