#!/usr/bin/env python3
"""Аудит синтаксических форм PICK по всем корпусам.

Форма-кандидат на удаление — та, что не встречается НИГДЕ: ни в bench-корпусе,
ни в eval-20, ни в примерах. Удалять форму, которая живёт в eval, нельзя:
это не сахар, а семантика с тестами. Запуск: python3 tools/syntax_audit.py
"""
import glob
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from bench.eval_tasks import TASKS  # noqa: E402

FORMS = {
    "пайплайн >>": r">>", "top(": r"top\(", "fields(": r"fields\(", "count(": r"count\(",
    "keys(": r"keys\(", "take(": r"take\(", "страж ?": r"^\?", "@wait": r"@wait",
    "for": r"^for ", "параллель ||": r"\|\|", "компенсация !": r" ! ", "ретраи ^N": r"\^\d",
    "@fx": r"@fx", "done when": r"done when", "!budget": r"!budget",
}


def main():
    corpus = [Path(f).read_text() for f in glob.glob("bench/corpus/*.pick")]
    corpus += [Path(f).read_text() for f in glob.glob("examples/*.pick")]
    eval_src = [task["src"] for task in TASKS]
    print(f"{'Форма':18} {'bench+examples':>14} {'eval20':>7}  вердикт")
    candidates = []
    for name, pat in FORMS.items():
        a = sum(len(re.findall(pat, t, re.M)) for t in corpus)
        b = sum(len(re.findall(pat, t, re.M)) for t in eval_src)
        verdict = "ЖИВЁТ (семантика, не сахар)" if a + b else "КАНДИДАТ НА УДАЛЕНИЕ"
        if not a + b:
            candidates.append(name)
        print(f"{name:18} {a:14} {b:7}  {verdict}")
    print("\nКандидатов на удаление:", candidates or "нет — все формы используются")


if __name__ == "__main__":
    main()
