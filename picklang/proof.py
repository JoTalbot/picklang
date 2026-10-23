"""Консервативный анализ порядка approval на всех структурных путях PICK.

Это не доказательство поведения хост-функций: эффекты распознаются по @fx и
известным суффиксам имён. Для одобрения нужны @wait approval и положительный
guard; одобрение локальной ветви не переносится за её границу.
"""
from __future__ import annotations

from typing import Any

from .parser import parse

DANGEROUS_SUFFIXES = ("charge", "refund", "send", "delete", "remove", "pay", "transfer", "drop")


def _calls(expr):
    if isinstance(expr, (tuple, list)):
        if expr and expr[0] == "call":
            yield expr[1]
        for value in expr:
            yield from _calls(value)


def _positive_approval(expr):
    if expr == ("var", "approval"):
        return True
    if isinstance(expr, tuple) and len(expr) == 4 and expr[:2] == ("cmp", "=="):
        for variable, literal in ((expr[2], expr[3]), (expr[3], expr[2])):
            if (variable == ("var", "approval") and isinstance(literal, tuple)
                    and len(literal) == 2 and literal[0] == "lit" and literal[1] is True):
                return True
    return False


def prove_safety(source: str) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    proof: list[str] = []

    def inspect(expr, step, approved):
        dangerous = (step.effect in ("money", "irreversible") or
                     any(name.split(".")[-1] in DANGEROUS_SUFFIXES for name in _calls(expr)))
        if dangerous:
            if approved:
                proof.append(f"строка {step.line}: опасный шаг покрыт проверенным approval")
            else:
                violations.append({"line": step.line,
                                   "problem": "опасный шаг достижим без @wait approval и положительного guard"})

    def analyze(steps, approved=False, received=False):
        for step in steps:
            if step.kind == "conditional":
                # Не пытаемся угадать значения фактов: проверяем ВСЕ ветви.
                for _, body in step.cases:
                    analyze(body, approved, received)
                # Runtime восстанавливает approval-контекст при выходе из ветки.
                continue
            if step.kind == "wait":
                if step.else_step is not None:
                    analyze([step.else_step], approved, received)
                if step.signal == "approval":
                    # Таймаут/fallback не доказывает получение сигнала.
                    received = step.timeout is None and step.else_step is None
                    approved = False
                continue
            if step.kind == "loop":
                inspect(step.expr, step, approved)
                if step.loop_var == "approval":
                    approved = received = False
                analyze([step.body], approved, received)
                if step.body.target == "approval":
                    approved = received = False
                continue
            if step.kind == "parallel":
                for target, expr in step.branches:
                    inspect(expr, step, approved)
                if any(target == "approval" for target, _ in step.branches):
                    approved = received = False
                continue
            if step.kind not in {"goal", "assign", "expr", "guard", "out", "budget", "donewhen"}:
                violations.append({"line": step.line, "problem": f"неподдерживаемый шаг {step.kind}"})
                continue
            inspect(step.expr, step, approved)
            if step.kind == "guard":
                inspect(step.target, step, approved)
                if received and _positive_approval(step.expr):
                    approved = True
                    proof.append(f"строка {step.line}: положительный guard после получения approval")
            if step.compensation is not None:
                inspect(step.compensation, step, approved)
            if step.kind == "assign" and step.target == "approval":
                approved = received = False

    analyze(parse(source))
    return {"safe": not violations, "violations": violations, "proof": proof}
