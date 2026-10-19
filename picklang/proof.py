"""П.10 — Статическое доказательство безопасности программы.

Теорема о графе шагов: необратимый/денежный шаг недостижим без предшествующего
@wait approval в той же программе. Проверяется до запуска, а не тестами на удачу.
"""
from __future__ import annotations

from typing import Any

from .parser import parse

DANGEROUS_SUFFIXES = ("charge", "refund", "send", "delete", "remove", "pay", "transfer", "drop")


def prove_safety(source: str) -> dict[str, Any]:
    steps = parse(source)
    violations: list[dict[str, Any]] = []
    proof: list[str] = []
    approved_seen = False
    for step in steps:
        if step.kind == "wait" and step.signal == "approval":
            approved_seen = True
            proof.append(f"строка {step.line}: одобрение получено до последующих шагов")
            continue
        dangerous = step.effect in ("money", "irreversible")
        if not dangerous and step.expr is not None:
            def calls(e):
                if isinstance(e, tuple):
                    if e[0] == "call":
                        yield e[1]
                    for x in e[1:]:
                        if isinstance(x, tuple):
                            yield from calls(x)
                        elif isinstance(x, list):
                            for y in x:
                                if isinstance(y, tuple):
                                    yield from calls(y)
            dangerous = any(c.split(".")[-1] in DANGEROUS_SUFFIXES for c in calls(step.expr))
        if dangerous:
            if approved_seen:
                proof.append(f"строка {step.line}: опасный шаг покрыт предшествующим одобрением")
            else:
                violations.append({"line": step.line,
                                   "problem": "необратимый/денежный шаг достижим без @wait approval"})
    return {"safe": not violations, "violations": violations, "proof": proof}
