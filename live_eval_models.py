#!/usr/bin/env python3
"""П.7 — Живой eval: одни и те же задачи намерения — разным моделям.

Метрики честности агентной системы, а не «короче текст»: вернула ли модель
валидное намерение с первой попытки, собралась ли программа, сколько токенов
и секунд это стоило. Запуск:
  python3 live_eval_models.py --key-file <файл> [--write]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from picklang.genesis import generate                      # noqa: E402
from picklang.live_agent import chat, extract_json, load_key  # noqa: E402
from picklang.check import check_source                    # noqa: E402
from picklang.schema import intent_schema                  # noqa: E402

SYSTEM = """Ты пишешь намерение для генератора программ. Верни ТОЛЬКО JSON.
Формат: {"goal": "...", "params": [...], "steps": [{"do": "fn", "args": [...], "as": "var",
"undo": "fn(...)"} | {"guard": "условие", "fail": "причина"} | {"wait": "сигнал"}], "out": {...}}
Функции: bank.charge(user, amt)->{ok,tx}; bank.refund(tx); stock.reserve(id); stock.release(id);
mail.send(user, text); db.get(id)->{paid, tx}; db.mark(id)"""

TASKS = {
    "pay": "Оплата: спиши bank.charge, при отказе guard, результат {tx: p.tx}. goal pay, params user, amt.",
    "reserve": "Резерв: stock.reserve с компенсацией stock.release, результат r. goal reserve, params id.",
    "refund": "Возврат: db.get, guard по o.paid, bank.refund(o.tx), результат o.tx. goal refund, params id.",
}
MODELS = ["poolside/laguna-s-2.1:free", "cohere/north-mini-code:free"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    key = load_key(args.key_file)
    rows = []
    for model in MODELS:
        for name, task in TASKS.items():
            t0 = time.time()
            status, usage, seconds = "OK", {}, 0.0
            try:
                try:
                    text, usage = chat(key, model, [{"role": "system", "content": SYSTEM},
                                                    {"role": "user", "content": task}],
                                       timeout=60, json_schema=intent_schema())
                except Exception:
                    # constrained decoding провайдером не поддержан — повтор без схемы
                    text, usage = chat(key, model, [{"role": "system", "content": SYSTEM},
                                                    {"role": "user", "content": task}], timeout=60)
                    status = "OK (без схемы)"
                intent = extract_json(text)
                src = generate(intent)
                diags = [d for d in check_source(src) if d["code"] == "UNKNOWN_FN"]
                if diags:
                    status = "схема ок, но статич. контроль ругается"
            except Exception as e:
                status = f"FAIL {type(e).__name__}"
            seconds = time.time() - t0
            total = usage.get("total_tokens", "?") if usage else "?"
            rows.append((model, name, status, total, round(seconds, 1)))
            print(f"{model:38} {name:9} {status:22} токенов: {total} за {seconds:.0f}с")
    if args.write:
        lines = ["# Живой eval моделей на задачах намерения (live_eval_models.py)", "",
                 "| Модель | Задача | Итог | Токенов | Секунд |", "|---|---|---|---:|---:|"]
        lines += [f"| {m} | {n} | {s} | {t} | {sec} |" for m, n, s, t, sec in rows]
        ok = sum(1 for r in rows if r[2] == "OK")
        lines += ["", f"**Валидных намерений с первой попытки: {ok}/{len(rows)}.** "
                      "Прогон на бесплатном тарифе: недоступность модели — тоже результат, она записана как FAIL."]
        Path("docs/LIVE_EVAL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/LIVE_EVAL.md обновлён")


if __name__ == "__main__":
    main()
