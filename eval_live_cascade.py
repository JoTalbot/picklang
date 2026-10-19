#!/usr/bin/env python3
"""Живой замер каскада моделей на OpenRouter (free tier, $0).

6 задач: модель пишет программу PICK по описанию. Валидатор строгий:
программа парсится, проходит check против реестра и ИСПОЛНЯЕТСЯ на моках
до ожидаемого результата. Каскад: сначала крошечные модели, сильные —
только если дешёвые не справились.

Запуск: python3 eval_live_cascade.py --key-file /path/key [--write]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from picklang.cascade import ModelCascade          # noqa: E402
from picklang.check import check_source             # noqa: E402
from picklang.interpreter import run_program        # noqa: E402
from picklang.live_agent import load_key            # noqa: E402
from picklang.stdlib import with_stdlib             # noqa: E402

GRAMMAR = """Ты пишешь программы на языке PICK. Верни ТОЛЬКО код, без пояснений и без markdown.
Синтаксис (одна строка = один шаг):
> name(param)            — заголовок, параметры через запятую
x = fn(a, b)             — вызов функции хоста, результат в x
x = fn(a) >> top(1)      — пайплайн: top(n), fields("k"), count()
? x.ok -> fail "msg"     — страж: если условие истинно, упасть
out x                    — результат программы
Доступны только перечисленные функции хоста. Обращение к полям: p.tx
Пример:
> t(u)
p = bank.charge(u, 5)
out p.tx
"""

TASKS = [
    {"name": "charge_tx", "task": "Спиши с пользователя u сумму 5 через bank.charge(u, amount) -> {ok, tx}. Верни tx.",
     "tools": ["bank.charge"], "registry": {"bank.charge": lambda u, a: {"ok": True, "tx": "t1"}},
     "inputs": {"u": "x"}, "expected": "t1"},
    {"name": "search_top1", "task": "Найди через web.search(q) -> список. Верни первый элемент (top(1)).",
     "tools": ["web.search"], "registry": {"web.search": lambda q: [7, 8, 9]},
     "inputs": {"q": "x"}, "expected": [7]},
    {"name": "double_last", "task": "Для каждого элемента списка xs примени dbl(i) -> число, цикл: for i in xs: y = dbl(i). Верни последнее y.",
     "tools": ["dbl"], "registry": {"dbl": lambda i: i * 2},
     "inputs": {"xs": [2, 3]}, "expected": 6},
    {"name": "fields_a", "task": "Получи d.get(x) -> {a, b}. Верни только поле a через пайплайн fields(\"a\").",
     "tools": ["d.get"], "registry": {"d.get": lambda x: {"a": 1, "b": 2}},
     "inputs": {"x": 1}, "expected": {"a": 1}},
    {"name": "count_results", "task": "Найди через web.search(q) -> список. Верни количество результатов через пайплайн count().",
     "tools": ["web.search"], "registry": {"web.search": lambda q: [1, 2]},
     "inputs": {"q": "x"}, "expected": 2},
    {"name": "guard_charge", "task": "Спиши bank.charge(u, 5) -> {ok, tx}. Если p.ok ложно — упасть: ? p.ok -> fail нельзя писать наоборот: страж срабатывает когда условие ИСТИННО, поэтому проверяй отрицание нельзя; используй условие вида ? p.ok -> fail только если нужно упасть при успехе. Здесь: верни p.tx если ok, иначе программа должна упасть через страж на поле ok (подсказка: проверь, что ok истинно, стражем на обратное условие невозможно — просто верни p.tx).",
     "tools": ["bank.charge"], "registry": {"bank.charge": lambda u, a: {"ok": True, "tx": "t9"}},
     "inputs": {"u": "x"}, "expected": "t9"},
]


def clean(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


def make_validator(task):
    registry = with_stdlib(task["registry"])
    def valid(text: str) -> bool:
        src = clean(text)
        try:
            errors = [i for i in check_source(src, registry) if i.get("level") == "error"]
            if errors:
                return False
            out, _, _ = run_program(src, registry, dict(task["inputs"]), prefer_aot=False)
            return out == task["expected"]
        except Exception:
            return False
    return valid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--timeout", type=int, default=45)
    args = ap.parse_args()
    key = load_key(args.key_file)

    tiers = [["liquid/lfm-2.5-2.6b:free", "poolside/laguna-xs-2.1:free"],
             ["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free"]]
    results = []
    for task in TASKS:
        prompt = GRAMMAR + f"\nФункции хоста: {', '.join(task['tools'])}\nВход: {list(task['inputs'])}\nЗадача: {task['task']}\n"
        cascade = ModelCascade(tiers, per_model_timeout=args.timeout)
        t0 = time.time()
        row = {"task": task["name"], "ok": False, "tier": None, "model": None,
               "attempts": 0, "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0}
        try:
            res = cascade.run(key, [{"role": "user", "content": prompt}], make_validator(task))
            row.update(ok=True, tier=res["tier"], model=res["model"])
        except Exception as e:
            row["error"] = str(e)[:200]
        row["seconds"] = round(time.time() - t0, 1)
        row["attempts"] = len(cascade.log)
        for entry in cascade.log:
            u = entry.get("usage") or {}
            row["prompt_tokens"] += u.get("prompt_tokens", 0) or 0
            row["completion_tokens"] += u.get("completion_tokens", 0) or 0
        row["log"] = cascade.log
        results.append(row)
        print(f"[{task['name']}] ok={row['ok']} tier={row['tier']} model={row['model']} "
              f"попыток={row['attempts']} {row['seconds']}с tok={row['prompt_tokens']}+{row['completion_tokens']}", flush=True)

    ok = sum(r["ok"] for r in results)
    cheap = sum(1 for r in results if r["ok"] and r["tier"] == 0)
    total_tok = sum(r["prompt_tokens"] + r["completion_tokens"] for r in results)
    print(f"\nИТОГ: {ok}/{len(results)} задач, из них дешёвым tier-0 решено {cheap}, "
          f"всего токенов {total_tok}, попыток {sum(r['attempts'] for r in results)}")
    Path("bench/live_cascade_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.write:
        lines = ["# Живой каскад моделей (OpenRouter, free tier, $0)", "",
                 "Валидатор: парсинг + check + исполнение на моках до ожидаемого результата.", "",
                 "| Задача | OK | Tier | Модель | Попыток | Сек | Токены |", "|---|---|---|---|---:|---:|---:|"]
        for r in results:
            lines.append(f"| {r['task']} | {r['ok']} | {r['tier']} | {r['model']} | {r['attempts']} | "
                         f"{r['seconds']} | {r['prompt_tokens']}+{r['completion_tokens']} |")
        lines += ["", f"Итого: {ok}/{len(results)}; дешёвым tier-0 решено {cheap}; всего токенов {total_tok}.",
                  "Оговорка: free tier нестабилен (429/зависания случались в прошлых прогонах) — "
                  "цифры одного прогона, а не статистика."]
        Path("docs/LIVE_CASCADE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/LIVE_CASCADE.md обновлён")


if __name__ == "__main__":
    main()
