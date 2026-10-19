#!/usr/bin/env python3
"""Fair token-cost benchmark for one durable subscription task and amortization.

Token counts measure model-authored text, not generated PICK code. The report
includes the one-time vocabulary/bootstrap cost and shows when reuse pays off.
This is a token-only generation comparison; semantic equivalence and runtime
latency require separate measurements.
"""
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from durable_weave.pickrun import run_pick          # noqa: E402
from picklang.fix import suggest_patches            # noqa: E402
from picklang.genesis import generate               # noqa: E402
from picklang.vocab import Vocabulary               # noqa: E402

INTENT = {
    "goal": "subscribe", "params": ["user", "amt"],
    "steps": [
        {"do": "db.create_sub", "args": ["user"], "as": "sub", "undo": "db.cancel_sub(sub.id)"},
        {"use": "pay", "args": ["user", "amt"]},
        {"use": "notify", "args": ["user"]},
    ],
    "out": {"sub": "sub.id", "tx": "p.tx"},
}

VOCAB = {
    "pay": (["user", "amt"],
            "p = bank.charge({user}, {amt}) ! bank.refund(p.tx)\n? p.ok -> fail \"банк отклонил списание\""),
    "notify": (["user"],
               "mail.send({user}, \"Подписка активна\")\nlog.event(\"subscription_active\")"),
}


def tokens(text: str, enc) -> int:
    return len(enc.encode(text)) if enc else round(len(text) / 4)


def calculate_amortized_costs(vocab_tokens: int, pick_task_tokens: int,
                              python_task_tokens: int,
                              task_counts=(1, 10, 100)):
    """Compare total and per-task model-authored tokens, including PICK setup."""
    if min(vocab_tokens, pick_task_tokens, python_task_tokens) < 0:
        raise ValueError("token costs must be non-negative")
    rows = []
    for count in task_counts:
        if count < 1:
            raise ValueError("task counts must be positive")
        pick_total = vocab_tokens + count * pick_task_tokens
        python_total = count * python_task_tokens
        rows.append({
            "tasks": count,
            "pick_total": pick_total,
            "python_total": python_total,
            "pick_per_task": pick_total / count,
            "python_per_task": python_total / count,
            "saving_multiple": python_total / pick_total if pick_total else None,
        })
    return rows


def break_even_tasks(vocab_tokens: int, pick_task_tokens: int,
                     python_task_tokens: int):
    """Smallest task count at which PICK's cumulative token cost is lower."""
    per_task_saving = python_task_tokens - pick_task_tokens
    if per_task_saving <= 0:
        return None
    # Strictly lower, not merely equal.
    return max(1, math.floor(vocab_tokens / per_task_saving) + 1)


def main():
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        enc_name = "cl100k_base"
    except ImportError:
        enc, enc_name = None, "~символы/4 (tiktoken не установлен; приблизительно)"

    report = ["# Живой цикл задачи «подписка» и честная амортизация",
              "", f"Токенизатор: {enc_name}.",
              "",
              "Это сравнение токенов текста, отправляемого модели, а не общей производительности.",
              "Качество, эквивалентность поведения и время выполнения нужно оценивать отдельными тестами.",
              "Для PICK словарь считается один раз и затем переиспользуется; для классического варианта",
              "считается полный Python-файл и его полная пересылка при исправлении."]
    with tempfile.TemporaryDirectory() as tmp:
        vocab = Vocabulary(Path(tmp) / "vocab")
        for alias, (params, body) in VOCAB.items():
            vocab.add(alias, params, body)

        generated = vocab.expand(generate(INTENT))
        registry = {
            "db.create_sub": lambda user: {"id": "s1"},
            "db.cancel_sub": lambda id: None,
            "bank.charge": lambda user, amt: {"ok": True, "tx": "tx1"},
            "bank.refund": lambda tx: None,
            "mail.send": lambda user, text: {"id": "m1"},
            "log.event": lambda name: None,
        }
        broken = generated.replace("bank.charge", "bank.charg")
        patches = suggest_patches(broken, registry=registry)
        patch_text = patches[0]["patch"] if patches else ""

        calls = {"charge": 0}
        registry["bank.charge"] = lambda user, amt: calls.__setitem__("charge", calls["charge"] + 1) or {"ok": True, "tx": "tx1"}
        workdir = Path(tmp) / "run"
        r1, _ = run_pick(generated, registry, {"user": "u1", "amt": 500}, workdir, "sub-1", crash_after=1)
        r2, ex = run_pick(generated, registry, {"user": "u1", "amt": 500}, workdir, "sub-1")
        verified = ex.verify("sub-1")

        py_source = Path("examples/subscription.py").read_text(encoding="utf-8")
        vocab_source = json.dumps(VOCAB, ensure_ascii=False, separators=(",", ":"))
        intent_text = json.dumps(INTENT, ensure_ascii=False, separators=(",", ":"))
        vocab_cost = tokens(vocab_source, enc)
        intent_cost = tokens(intent_text, enc)
        patch_cost = tokens(patch_text, enc)
        python_cost = tokens(py_source, enc) * 2
        pick_task_cost = intent_cost + patch_cost

        report += ["",
                   "| Компонент | Токенов модели |",
                   "|---|---:|",
                   f"| Первичная настройка словаря PICK (один раз) | {vocab_cost} |",
                   f"| Намерение для Genesis на задачу | {intent_cost} |",
                   f"| Исправление патчем на задачу | {patch_cost} |",
                   f"| Полная программа Python + полная пересылка при исправлении | {python_cost} |",
                   f"| Генерация PICK после намерения (машина, не LLM) | 0 |",
                   "",
                   "## Суммарная стоимость с учётом повторного использования",
                   "",
                   "| Задач | PICK всего | Python всего | PICK на задачу | Python на задачу | Соотношение Python/PICK |",
                   "|---:|---:|---:|---:|---:|---:|"]
        amortized = calculate_amortized_costs(vocab_cost, pick_task_cost, python_cost)
        for row in amortized:
            ratio = f'{row["saving_multiple"]:.2f}x' if row["saving_multiple"] is not None else "н/д"
            report.append(
                f'| {row["tasks"]} | {row["pick_total"]} | {row["python_total"]} | '
                f'{row["pick_per_task"]:.1f} | {row["python_per_task"]:.1f} | {ratio} |'
            )
        breakeven = break_even_tasks(vocab_cost, pick_task_cost, python_cost)
        if breakeven is None:
            report.append("\n**Точка окупаемости не достигается:** стоимость PICK на каждую задачу не ниже Python.")
        else:
            report.append(f"\nТочка окупаемости словаря: **{breakeven} задач** при указанных предположениях.")
        report += ["",
                   "## Проверка durable-выполнения",
                   "",
                   f"Первый запуск: {r1.status}; повторный запуск: {r2.status}; списаний у банка: {calls['charge']}; "
                   f"verify: {verified['verified']}; событий в журнале: {verified['events']}.",
                   "",
                   "## Ограничения",
                   "",
                   "- Это измерение токенов, не цены в долларах: цены зависят от конкретной модели и тарифа.",
                   "- Учитывается сериализованное содержимое словаря, но не системный промпт, история чата, ответы модели, "
                   "повторные попытки и токены инструментов. Полный end-to-end учёт нужно добавить отдельно.",
                   "- Python-файл и PICK-словарь не доказаны как семантически эквивалентные реализации; коэффициент нельзя "
                   "трактовать как доказательство общего превосходства.",
                   "- Если исправления требуют разных объёмов текста, следует считать реальные входные/выходные токены "
                   "из API-телеметрии, а не предполагать полную пересылку."]
        print("\n".join(report))
        print()
        print(f"execution: {r1.status} -> {r2.status}, charges={calls['charge']}, verified={verified['verified']}")
        if "--write" in sys.argv:
            Path("docs/LIVE_CYCLE.md").write_text("\n".join(report) + "\n", encoding="utf-8")
            print("docs/LIVE_CYCLE.md обновлён")


if __name__ == "__main__":
    main()
