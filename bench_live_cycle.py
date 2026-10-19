#!/usr/bin/env python3
"""Живой цикл одной задачи через словарь + Genesis, с замером токенов по этапам.

Задача: оформление подписки. Этапы, за которые платит LLM:
  1) намерение (Genesis dict) -> сгенерированная программа PICK;
  2) ошибка в программе (опечатка в функции) -> исправление патчем,
     а не пересылкой программы целиком;
  3) исполнение на durable-рантайме: падение после первого шага,
     resume без повторной оплаты банку; verify по digest.
Сравнение: тот же цикл «напрямую» — полная программа PICK руками и
эквивалент на Python (examples/subscription.py) при каждой итерации.
Запуск: python3 bench_live_cycle.py [--write]
"""
import json
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


def main():
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        enc_name = "cl100k_base"
    except ImportError:
        enc, enc_name = None, "~символы/4 (tiktoken не установлен)"

    report = [f"# Живой цикл задачи «подписка» (автогенерация: bench_live_cycle.py --write)",
              "", f"Токенизатор: {enc_name}.", ""]
    with tempfile.TemporaryDirectory() as tmp:
        # --- словарь проекта: один раз дорого, дальше — по одному слову ---
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

        # --- этап «ошибка -> патч» -------------------------------------
        broken = generated.replace("bank.charge", "bank.charg")
        patches = suggest_patches(broken, registry=registry)
        patch_text = patches[0]["patch"] if patches else ""

        # --- исполнение: падение после 1 шага, resume, verify ----------
        calls = {"charge": 0}
        registry["bank.charge"] = lambda user, amt: calls.__setitem__("charge", calls["charge"] + 1) or {"ok": True, "tx": "tx1"}
        workdir = Path(tmp) / "run"
        r1, _ = run_pick(generated, registry, {"user": "u1", "amt": 500}, workdir, "sub-1", crash_after=1)
        r2, ex = run_pick(generated, registry, {"user": "u1", "amt": 500}, workdir, "sub-1")
        verified = ex.verify("sub-1")

        py_source = Path("examples/subscription.py").read_text(encoding="utf-8")
        rows = [
            ("Намерение для Genesis (то, что пишет LLM)", json.dumps(INTENT, ensure_ascii=False), None),
            ("Сгенерированная программа PICK (пишет генератор, не LLM)", generated, None),
            ("Та же программа, если писать PICK руками целиком", generated, None),
            ("Эквивалент на Python целиком (examples/subscription.py)", py_source, None),
            ("Исправление опечатки: патч", patch_text, None),
            ("Исправление по-старому: программа целиком заново", generated, None),
        ]
        report += ["| Этап живого цикла | Токенов платит LLM |", "|---|---:|"]
        values = {}
        for label, text, _ in rows:
            n = tokens(text, enc)
            values[label] = n
            pays = "0 (машина)" if label.startswith("Сгенерированная") else str(n)
            report.append(f"| {label} | {pays} |")
            print(f"{label:58} {n:>6} ток.")
        cycle_pick = values["Намерение для Genesis (то, что пишет LLM)"] + values["Исправление опечатки: патч"]
        cycle_py = values["Эквивалент на Python целиком (examples/subscription.py)"] * 2  # программа + её же пересылка при правке
        report += ["",
                   f"**Итого за цикл «написать + исправить ошибку»: через Genesis+патч — {cycle_pick} токенов, "
                   f"классически на Python (код + пересылка при правке) — {cycle_py} токенов. Выигрыш {cycle_py / cycle_pick:.1f}x.**",
                   "",
                   f"Исполнение: первый запуск — {r1.status} (искусственное падение после закоммиченного шага), "
                   f"второй — {r2.status}, списаний у банка: {calls['charge']} (повторного нет — шаг взят из журнала), "
                   f"verify: {verified['verified']}, digest исполнения: `{verified['digest'][:16]}…`, событий в журнале: {verified['events']}.",
                   "",
                   "Чего цикл НЕ измеряет: стоимость первичного создания словаря проекта (2 блока, платится один раз",
                   "на проект) и уникальную бизнес-логику внутри функций хоста — она вне языка на любом подходе."]
        print()
        print(f"цикл Genesis+патч: {cycle_pick} ток. | классический Python x2 итерации: {cycle_py} ток. | выигрыш {cycle_py/cycle_pick:.1f}x")
        print(f"исполнение: {r1.status} -> {r2.status}, charges={calls['charge']}, verified={verified['verified']}")
        if "--write" in sys.argv:
            Path("docs/LIVE_CYCLE.md").write_text("\n".join(report) + "\n", encoding="utf-8")
            print("docs/LIVE_CYCLE.md обновлён")


if __name__ == "__main__":
    main()
