#!/usr/bin/env python3
"""П.12 — Одноразовая генерация против ReAct-цикла: на сколько вызовов LLM меньше.

ReAct: модель зовётся на каждый шаг (подумал -> шаг -> подумал...).
PICK: одна генерация намерения на всю программу, дальше рантайм.
Считаем на eval-корпусе из 20 задач по фактическому числу шагов программы.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pathlib import Path as _P

from picklang.parser import parse   # noqa: E402

TASKS = [{"src": f.read_text(encoding="utf-8")} for f in sorted(_P("bench/corpus").glob("*.pick"))]


def main():
    react_calls = pick_calls = total_steps = 0
    for t in TASKS:
        steps = [s for s in parse(t["src"]) if s.kind in ("assign", "expr", "guard", "wait")]
        total_steps += len(steps)
        react_calls += len(steps)      # вызов модели на шаг + финальный ответ
        pick_calls += 1                # одна генерация намерения на задачу
    print(f"задач: {len(TASKS)}, шагов суммарно: {total_steps}")
    print(f"ReAct: ~{react_calls + len(TASKS)} вызовов модели | PICK one-shot: {pick_calls} вызовов")
    print(f"вызовов меньше в {(react_calls + len(TASKS)) / pick_calls:.1f} раза, "
          "а каждый вызов ReAct ещё и тащит растущую историю шагов")


if __name__ == "__main__":
    main()
