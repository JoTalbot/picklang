#!/usr/bin/env python3
"""Полный живой цикл с настоящей LLM через OpenRouter.

Один и тот же task card даётся модели двумя способами:
  A) наш путь: модель пишет только намерение (Genesis dict) -> генератор
     собирает PICK -> словарь разворачивает блоки -> durable-исполнение
     с падением и resume -> verify;
  B) классика: модель пишет эквивалентный Python-код целиком.
Считаем usage из ответов API (prompt/completion токены) по каждому вызову,
а не оценки «на глаз». Ошибки генерации: сначала автопатчи fix.py,
потом — компактный запрос модели починить только намерение.

Запуск:
  python3 live_cycle_openrouter.py --key-file /path/to/openrouter.txt [--write]
Ключ в репозиторий и в отчёт не попадает.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from durable_weave.pickrun import run_pick                     # noqa: E402
from picklang.genesis import generate                          # noqa: E402
from picklang.live_agent import DEFAULT_MODELS, chat, extract_json, load_key  # noqa: E402
from picklang.vocab import Vocabulary                          # noqa: E402

FUNCTIONS = """Функции хоста (вызывать только их):
db.create_sub(user) -> {id}; db.cancel_sub(id)
bank.charge(user, amt) -> {ok, tx}; bank.refund(tx)
mail.send(user, text) -> {id}; log.event(name)
Блоки словаря (использовать через {"use": ...}):
pay(user, amt) — списание с компенсацией и стражем оплаты внутри
notify(user) — письмо пользователю и событие в лог"""

TASK = """Задача (task card): оформи подписку пользователя.
Шаги: создать подписку (компенсация — db.cancel_sub по sub.id), списать оплату
блоком pay, уведомить блоком notify. Программа: goal subscribe, параметры user, amt.
Результат: {"sub": sub.id, "tx": p.tx}."""

INTENT_SYSTEM = f"""Ты пишешь намерение для генератора программ. Верни ТОЛЬКО JSON, без пояснений.
Формат: {{"goal": "...", "params": [...], "steps": [...], "out": {{...}}}}
Шаг: {{"do": "fn", "args": [...], "as": "var", "undo": "fn(...)"}} или {{"use": "блок", "args": [...]}}
или {{"guard": "условие", "fail": "причина"}} или {{"wait": "сигнал"}}.
Аргументы-строки, совпадающие с именами переменных, — ссылки на них.
{FUNCTIONS}"""

PYTHON_SYSTEM = f"""Ты пишешь код. Верни ТОЛЬКО функцию Python subscribe(user, amt, db, bank, mail, log),
без пояснений и без markdown. Нужны компенсации при сбое (откаты в обратном порядке).
{FUNCTIONS}"""


def try_models(key, messages):
    errors = []
    for model in DEFAULT_MODELS:
        try:
            text, usage = chat(key, model, messages, timeout=90)
            if text.strip():
                return model, text, usage
            errors.append(f"{model}: пустой ответ")
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError("все модели недоступны: " + " | ".join(errors))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file")
    ap.add_argument("--model", help="переопределить модель (иначе перебор бесплатных)")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    key = load_key(args.key_file)
    if args.model:
        global DEFAULT_MODELS
        DEFAULT_MODELS = [args.model]

    # --- A: намерение через Genesis -------------------------------------
    model_a, intent_text, usage_a = try_models(key, [
        {"role": "system", "content": INTENT_SYSTEM},
        {"role": "user", "content": TASK}])
    intent = extract_json(intent_text)

    with tempfile.TemporaryDirectory() as tmp:
        vocab = Vocabulary(Path(tmp) / "vocab")
        vocab.add("pay", ["user", "amt"],
                  "p = bank.charge({user}, {amt}) ! bank.refund(p.tx)\n? p.ok -> fail \"банк отклонил списание\"")
        vocab.add("notify", ["user"], "mail.send({user}, \"Подписка активна\")\nlog.event(\"subscription_active\")")
        source = vocab.expand(generate(intent))
        charges = {"n": 0}
        registry = {
            "db.create_sub": lambda user: {"id": "s1"},
            "db.cancel_sub": lambda id: None,
            "bank.charge": lambda user, amt: charges.__setitem__("n", charges["n"] + 1) or {"ok": True, "tx": "tx1"},
            "bank.refund": lambda tx: None,
            "mail.send": lambda user, text: {"id": "m1"},
            "log.event": lambda name: None,
        }
        r1, _ = run_pick(source, registry, {"user": "u1", "amt": 500}, Path(tmp) / "run", "sub-live", crash_after=1)
        r2, ex = run_pick(source, registry, {"user": "u1", "amt": 500}, Path(tmp) / "run", "sub-live")
        verified = ex.verify("sub-live")

    # --- B: классика — Python целиком ------------------------------------
    model_b, python_text, usage_b = try_models(key, [
        {"role": "system", "content": PYTHON_SYSTEM},
        {"role": "user", "content": TASK}])
    python_ok = True
    try:
        compile(python_text, "<model-python>", "exec")
    except SyntaxError:
        python_ok = False

    def total(u):
        return u.get("total_tokens") or (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))

    lines = ["# Живой LLM-цикл через OpenRouter (автогенерация: live_cycle_openrouter.py --write)", "",
             f"Модели: намерение — `{model_a}`, Python — `{model_b}` (бесплатный тариф, цена $0).",
             "Токены — фактический usage из ответов API, не оценка.", "",
             "| Путь | prompt | completion | всего |", "|---|---:|---:|---:|",
             f"| A: намерение Genesis (пишет LLM) | {usage_a.get('prompt_tokens', '?')} | {usage_a.get('completion_tokens', '?')} | {total(usage_a)} |",
             f"| B: Python целиком (пишет LLM) | {usage_b.get('prompt_tokens', '?')} | {usage_b.get('completion_tokens', '?')} | {total(usage_b)} |", "",
             f"Сгенерированная программа PICK ({len(source.splitlines())} строк) написана генератором, модель за неё не платила.",
             f"Исполнение: {r1.status} (падение после закоммиченного шага) -> {r2.status}, "
             f"списаний у банка: {charges['n']}, verify: {verified['verified']}, событий: {verified['events']}.",
             f"Python от модели: синтаксически {'валиден' if python_ok else 'НЕ валиден'} (не исполнялся — недоверенный код).", "",
             "## Намерение модели", "```json", json.dumps(intent, ensure_ascii=False, indent=1), "```", "",
             "## Программа, собранная генератором", "```pick", source.rstrip(), "```"]
    print("\n".join(lines[:14]))
    print(f"\nИтог: путь A {total(usage_a)} ток. против пути B {total(usage_b)} ток. "
          f"(completion: {usage_a.get('completion_tokens')} против {usage_b.get('completion_tokens')})")
    if args.write:
        Path("docs/LIVE_LLM_CYCLE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/LIVE_LLM_CYCLE.md обновлён")


if __name__ == "__main__":
    main()
