#!/usr/bin/env python3
"""Живой замер каскада моделей через OpenRouter или Groq API.

6 задач: модель пишет программу PICK по описанию. Валидатор строгий:
программа парсится, проходит check против реестра и ИСПОЛНЯЕТСЯ на моках
до ожидаемого результата. Каскад: сначала крошечные модели, сильные —
только если дешёвые не справились.

Запуск: python3 eval_live_cascade.py --key-file /path/key [--write]
"""
import argparse
import json
import os
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
    {"name": "double_value", "task": "Удвой число i через dbl(i) -> число. Верни результат вызова.",
     "tools": ["dbl"], "registry": {"dbl": lambda i: i * 2},
     "inputs": {"i": 3}, "expected": 6},
    {"name": "fields_a", "task": "Получи d.get(x) -> {a, b}. Верни только поле a через пайплайн fields(\"a\").",
     "tools": ["d.get"], "registry": {"d.get": lambda x: {"a": 1, "b": 2}},
     "inputs": {"x": 1}, "expected": {"a": 1}},
    {"name": "count_results", "task": "Найди через web.search(q) -> список. Верни количество результатов через пайплайн count().",
     "tools": ["web.search"], "registry": {"web.search": lambda q: [1, 2]},
     "inputs": {"q": "x"}, "expected": 2},
    {"name": "multi_step_subscribe", "task": "Создай подписку через db.create_sub(u) -> {id}, затем отправь уведомление mail.send(u, sub.id) -> {id}. Верни объект с id подписки в поле sub и id уведомления в поле message.",
     "tools": ["db.create_sub", "mail.send"],
     "registry": {"db.create_sub": lambda u: {"id": "s1"}, "mail.send": lambda u, sub_id: {"id": "m1"}},
     "inputs": {"u": "x"}, "expected": {"sub": "s1", "message": "m1"}},
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


def load_model_tiers(raw: str | None = None):
    """Read optional JSON model tiers; defaults depend on the selected provider."""
    raw = os.environ.get("PICK_LIVE_MODEL_TIERS") if raw is None else raw
    if not raw:
        if os.environ.get("PICK_LIVE_PROVIDER", "").lower() == "groq":
            return [["openai/gpt-oss-20b"], ["openai/gpt-oss-120b"]]
        return [["liquid/lfm-2.5-2.6b:free", "poolside/laguna-xs-2.1:free"],
                ["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free"]]
    try:
        tiers = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("PICK_LIVE_MODEL_TIERS must be valid JSON") from exc
    if (not isinstance(tiers, list) or not tiers or
            any(not isinstance(tier, list) or not tier or
                any(not isinstance(model, str) or not model.strip() for model in tier)
                for tier in tiers)):
        raise ValueError("PICK_LIVE_MODEL_TIERS must be a non-empty JSON array of non-empty model arrays")
    return tiers


def is_provider_rate_limit(error: object) -> bool:
    text = str(error).lower()
    return "429" in text or "rate limit" in text or "quota exceeded" in text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--require-all", action="store_true", help="return nonzero unless every live task passes")
    args = ap.parse_args()
    key = load_key(args.key_file)

    try:
        tiers = load_model_tiers()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    results = []
    quota_blocked = None
    for task in TASKS:
        prompt = GRAMMAR + f"\nФункции хоста: {', '.join(task['tools'])}\nВход: {list(task['inputs'])}\nЗадача: {task['task']}\n"
        cascade = ModelCascade(tiers, per_model_timeout=args.timeout)
        t0 = time.time()
        row = {"task": task["name"], "ok": False, "tier": None, "model": None,
               "attempts": 0, "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0}
        if quota_blocked:
            row["error"] = f"not attempted: provider quota/rate limit already reached ({quota_blocked})"
        else:
            try:
                res = cascade.run(key, [{"role": "user", "content": prompt}], make_validator(task))
                row.update(ok=True, tier=res["tier"], model=res["model"])
            except Exception as e:
                row["error"] = str(e)[:200]
                if is_provider_rate_limit(e):
                    quota_blocked = str(e)[:120]
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
        provider = os.environ.get("PICK_LIVE_PROVIDER", "openrouter").lower()
        lines = [f"# Живой каскад моделей ({provider})", "",
                 "Валидатор: парсинг + check + исполнение на моках до ожидаемого результата.", "",
                 "| Задача | OK | Tier | Модель | Попыток | Сек | Токены |", "|---|---|---|---|---:|---:|---:|"]
        for r in results:
            lines.append(f"| {r['task']} | {r['ok']} | {r['tier']} | {r['model']} | {r['attempts']} | "
                         f"{r['seconds']} | {r['prompt_tokens']}+{r['completion_tokens']} |")
        lines += ["", f"Итого: {ok}/{len(results)}; дешёвым tier-0 решено {cheap}; всего токенов {total_tok}.",
                  "Оговорка: доступность моделей и квоты зависят от провайдера и тарифа; "
                  "цифры одного прогона, а не статистика."]
        Path("docs/LIVE_CASCADE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/LIVE_CASCADE.md обновлён")
    if args.require_all and ok != len(results):
        print(f"ОШИБКА: --require-all задан, но прошли только {ok}/{len(results)} задач", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
