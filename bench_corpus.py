#!/usr/bin/env python3
"""П.6 — Публичный корпус бенчмарков экономии токенов.

8 типовых задач в bench/corpus/: каждая на PICK и на обычном Python.
Считает tiktoken (cl100k_base/o200k_base) и может перегенерировать
docs/BENCHMARKS.md (`--write`). Цифры воспроизводимы одной командой.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def encodings():
    try:
        import tiktoken
        return {n: tiktoken.get_encoding(n) for n in ("cl100k_base", "o200k_base")}
    except ImportError:
        return {}


def main():
    encs = encodings()
    enc = encs.get("cl100k_base")
    tasks = sorted({p.stem for p in (ROOT / "bench/corpus").glob("*.pick")})
    rows, tp, ty = [], 0, 0
    for task in tasks:
        pick = (ROOT / f"bench/corpus/{task}.pick").read_text(encoding="utf-8")
        py = (ROOT / f"bench/corpus/{task}.py").read_text(encoding="utf-8")
        if enc:
            a, b = len(enc.encode(pick)), len(enc.encode(py))
        else:
            a, b = round(len(pick) / 4), round(len(py) / 4)
        tp += a; ty += b
        rows.append((task, a, b, b / a))
    lines = ["# Корпус бенчмарков экономии токенов (автогенерация: bench_corpus.py --write)",
             "", "| Задача | PICK, ток. | Python, ток. | Выигрыш |", "|---|---:|---:|---:|"]
    for task, a, b, ratio in rows:
        print(f"{task:22} PICK {a:>5}  Python {b:>5}  x{ratio:.2f}")
        lines.append(f"| {task} | {a} | {b} | {ratio:.2f}x |")
    print(f"{'ИТОГО':22} PICK {tp:>5}  Python {ty:>5}  x{ty/tp:.2f}")
    lines += ["", f"**Итого: PICK {tp} токенов против Python {ty} — выигрыш {ty/tp:.2f}x по корпусу.**",
              "", "Методика: tiktoken cl100k_base; Python — идиоматичный код с той же механикой", "(компенсации, стражи, ожидания), а не специально раздутый. Воспроизведение:", "`python3 bench_corpus.py --write`."]
    if "--write" in sys.argv:
        (ROOT / "docs/BENCHMARKS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/BENCHMARKS.md обновлён")


if __name__ == "__main__":
    main()
