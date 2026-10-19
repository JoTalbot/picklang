#!/usr/bin/env python3
"""Честный замер: сколько токенов платит LLM за одну и ту же программу
в PICK, в обычном Python и в JSON-спецификации воркфлоу.

Токенизаторы: cl100k_base (GPT-4 класс) и o200k_base (GPT-4o класс), если
tiktoken установлен; иначе — грубая оценка ~4 символа/токен (помечается).
Плюс главный режим итераций: полный текст программы против diff-патча.
"""
from pathlib import Path

ROOT = Path(__file__).parent
FILES = [
    ("Сага оплаты · PICK", "examples/payment.pick"),
    ("Сага оплаты · Python", "examples/payment.py"),
    ("Сага оплаты · JSON-workflow", "examples/payment.json"),
    ("Агент+HITL · PICK", "examples/agent.pick"),
    ("Агент+HITL · Python", "examples/agent.py"),
    ("Итерация: diff-патч вместо всей программы", "examples/payment_patch.pickpatch"),
]


def counter():
    try:
        import tiktoken
        encs = {}
        for name in ("cl100k_base", "o200k_base"):
            try:
                encs[name] = tiktoken.get_encoding(name)
            except Exception:
                pass
        if encs:
            return encs, False
    except ImportError:
        pass
    return {}, True


def main():
    encs, fallback = counter()
    names = list(encs) or ["~оценка (символы/4)"]
    print("Токенизатор: " + (", ".join(names) + (" (fallback-оценка!)" if fallback else "")))
    print(f"{'Артефакт':52} {'симв.':>7} " + " ".join(f"{n:>14}" for n in names))
    rows = {}
    for label, rel in FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        counts = {n: len(e.encode(text)) for n, e in encs.items()}
        if fallback:
            counts = {names[0]: round(len(text) / 4)}
        rows[label] = counts
        print(f"{label:52} {len(text):>7} " + " ".join(f"{counts[n]:>14}" for n in names))
    enc = names[0]
    pick, py = rows["Сага оплаты · PICK"][enc], rows["Сага оплаты · Python"][enc]
    js = rows["Сага оплаты · JSON-workflow"][enc]
    apick, apy = rows["Агент+HITL · PICK"][enc], rows["Агент+HITL · Python"][enc]
    patch = rows["Итерация: diff-патч вместо всей программы"][enc]
    print()
    print(f"Сага:    PICK дешевле Python в {py/pick:.1f}x, дешевле JSON в {js/pick:.1f}x")
    print(f"Агент:   PICK дешевле Python в {apy/apick:.1f}x")
    print(f"Итерация: патч ({patch} ток.) вместо полной программы PICK ({pick} ток.) — ещё {pick/patch:.1f}x;")
    print(f"          а против пересылки Python-кода целиком — {py/patch:.1f}x на каждое изменение")


if __name__ == "__main__":
    main()
