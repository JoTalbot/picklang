"""Парсер PICK v0.1: построчный, без отступов-блоков, одна строка = один шаг.

Грамматика (полностью — в docs/LANGUAGE.md):
  > name(a, b)              вход/цель программы и её параметры
  x = expr                  привязка
  x = call ! undo_call      шаг саги: при позднейшем сбое выполнить undo (LIFO)
  ? expr -> fail "текст"    страж: условие ложно -> контролируемый отказ
  @wait signal              ждать внешний сигнал (человек/система)
  out expr                  результат программы
  expr                      выражение-шаг (обычно вызов)
Выражения: вызовы mod.fn(...), переменные, литералы, пайплайн a >> b >> c.
"""
from __future__ import annotations

import ast as py_ast
import re
from dataclasses import dataclass, field
from typing import Any


class PickSyntaxError(Exception):
    def __init__(self, line: int, text: str, hint: str):
        super().__init__(f"line {line}: {hint} :: {text.strip()}")
        self.line, self.text, self.hint = line, text, hint


@dataclass
class Step:
    kind: str                      # goal|assign|expr|guard|wait|out|budget|donewhen|parallel
    line: int
    target: str | None = None      # переменная слева от =
    expr: Any = None               # выражение (см. _parse_expr)
    compensation: Any = None       # выражение-компенсация после !
    signal: str | None = None
    name: str | None = None        # имя цели / сигнала
    params: list[str] = field(default_factory=list)
    budget: dict = field(default_factory=dict)      # для kind=budget
    branches: list = field(default_factory=list)     # для kind=parallel: [(target, expr)]
    retries: int = 0                                 # суффикс ^N на шаге
    effect: str | None = None                        # @fx read|write|money|irreversible
    timeout: int | None = None                       # для wait: секунды
    else_step: Any = None                            # для wait: шаг при таймауте
    loop_var: str | None = None                      # для kind=loop
    body: Any = None                                 # для kind=loop: вложенный Step


# Выражение кодируем простыми кортежами — IR должен быть JSON-сериализуемым:
# ("call", "bank.charge", [args]) | ("var", "x") | ("lit", value) | ("pipe", [exprs])
def _parse_expr(text: str, line: int, raw: str):
    text = text.strip()
    if not text:
        raise PickSyntaxError(line, raw, "пустое выражение")
    cmp_op = _find_cmp(text)
    if cmp_op:
        op, idx = cmp_op
        return ("cmp", op, _parse_expr(text[:idx], line, raw), _parse_expr(text[idx + len(op):], line, raw))
    if ">>" in text:
        return ("pipe", [_parse_expr(part, line, raw) for part in _split_top(text, ">>")])
    m = re.fullmatch(r"([A-Za-z_][\w.]*)\((.*)\)", text, re.S)
    if m:
        args_text = m.group(2).strip()
        args = [_parse_expr(a, line, raw) for a in _split_top(args_text, ",")] if args_text else []
        return ("call", m.group(1), args)
    if re.fullmatch(r"[A-Za-z_]\w*", text):
        return ("var", text)
    if re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+", text):
        return ("get", text)  # p.tx, draft.to — доступ к полю результата
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        items = []
        if inner:
            for part in _split_top(inner, ","):
                key, _, val = part.partition(":")
                items.append((key.strip().strip("\"'"), _parse_expr(val, line, raw)))
        return ("map", items)
    try:  # литералы: числа, строки, списки, true/false/null -> True/False/None как данные
        value = py_ast.literal_eval(text)
        return ("lit", value)
    except Exception:
        raise PickSyntaxError(line, raw, f"не понимаю выражение '{text}': ожидается вызов, переменная или литерал")


def _find_cmp(text: str):
    """Найти сравнение верхнего уровня (==, !=, >=, <=, >, <) вне скобок и строк."""
    depth, quote, i = 0, None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote: quote = None
            i += 1; continue
        if ch in "\"'": quote = ch; i += 1; continue
        if ch in "([{": depth += 1
        elif ch in ")]}": depth -= 1
        elif depth == 0:
            for op in ("==", "!=", ">=", "<="):
                if text.startswith(op, i): return op, i
            if text.startswith(">>", i):
                i += 2; continue
            if ch in "><": return ch, i
        i += 1
    return None


def _split_top(text: str, sep: str) -> list[str]:
    parts, depth, quote, cur = [], 0, None, ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch; cur += ch; i += 1; continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if depth == 0 and text.startswith(sep, i):
            parts.append(cur); cur = ""; i += len(sep); continue
        cur += ch; i += 1
    parts.append(cur)
    return [p.strip() for p in parts if p.strip() != ""]


def parse(source: str) -> list[Step]:
    steps: list[Step] = []
    for lineno, raw in enumerate(source.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            m = re.fullmatch(r">\s*([A-Za-z_]\w*)\(([^)]*)\)", line)
            if not m:
                raise PickSyntaxError(lineno, raw, "цель записывается как: > name(param1, param2)")
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]
            steps.append(Step("goal", lineno, name=m.group(1), params=params))
            continue
        if line.startswith("@wait"):
            rest = line[5:].strip()
            m = re.fullmatch(r"([A-Za-z_]\w*)(?:\s+:(\d+)([smh]))?(?:\s+else\s+(.+))?", rest)
            if not m:
                raise PickSyntaxError(lineno, raw, "ожидание записывается как: @wait approval или @wait approval :24h else <шаг>")
            timeout = None
            if m.group(2):
                timeout = int(m.group(2)) * {"s": 1, "m": 60, "h": 3600}[m.group(3)]
            else_step = None
            if m.group(4):
                else_step = parse(m.group(4))[0]
            steps.append(Step("wait", lineno, signal=m.group(1), timeout=timeout, else_step=else_step))
            continue
        if line.startswith("for "):
            m = re.fullmatch(r"for\s+([A-Za-z_]\w*)\s+in\s+(.+):\s*(.+)", line)
            if not m:
                raise PickSyntaxError(lineno, raw, "цикл записывается как: for item in items: <шаг одной строкой>")
            body = parse(m.group(3))[0]
            steps.append(Step("loop", lineno, loop_var=m.group(1),
                              expr=_parse_expr(m.group(2), lineno, raw), body=body))
            continue
        if line.startswith("?"):
            m = re.fullmatch(r"\?\s*(.+?)\s*->\s*fail\s+(.+)", line)
            if not m:
                raise PickSyntaxError(lineno, raw, 'страж записывается как: ? условие -> fail "причина"')
            steps.append(Step("guard", lineno, expr=_parse_expr(m.group(1), lineno, raw),
                              target=_parse_expr(m.group(2), lineno, raw)))
            continue
        if line.startswith("!budget"):
            budget = {}
            for part in line[len("!budget"):].split(","):
                m = re.fullmatch(r"\s*(steps|tokens|retries)<(\d+)\s*", part)
                if not m:
                    raise PickSyntaxError(lineno, raw, "бюджет записывается как: !budget steps<20, tokens<4000")
                budget[m.group(1)] = int(m.group(2))
            steps.append(Step("budget", lineno, budget=budget))
            continue
        if line.startswith("done when:"):
            exprs = [_parse_expr(e, lineno, raw) for e in _split_top(line[len("done when:"):], ",")]
            steps.append(Step("donewhen", lineno, expr=exprs))
            continue
        if " || " in line:
            branches = []
            for part in _split_top(line, "||"):
                if "=" in part:
                    lhs, rhs = part.split("=", 1)
                    branches.append((lhs.strip(), _parse_expr(rhs, lineno, raw)))
                else:
                    branches.append((None, _parse_expr(part, lineno, raw)))
            steps.append(Step("parallel", lineno, branches=branches))
            continue
        if line.startswith("out "):
            steps.append(Step("out", lineno, expr=_parse_expr(line[4:], lineno, raw)))
            continue
        retries, effect = 0, None
        m = re.search(r"\s\^(\d+)\s*$", line)
        if m:
            retries, line = int(m.group(1)), line[:m.start()]
        m = re.search(r"\s@fx\s+(read|write|money|irreversible)\s*$", line)
        if m:
            effect, line = m.group(1), line[:m.start()]
        if "=" in line and not line.startswith("="):
            lhs, rhs = line.split("=", 1)
            target = lhs.strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", target):
                raise PickSyntaxError(lineno, raw, "слева от = должно быть одно имя переменной")
            comp = None
            if " ! " in rhs:
                rhs, comp_text = rhs.split(" ! ", 1)
                comp = _parse_expr(comp_text, lineno, raw)
            steps.append(Step("assign", lineno, target=target,
                              expr=_parse_expr(rhs, lineno, raw), compensation=comp,
                              retries=retries, effect=effect))
            continue
        # выражение-шаг, возможно с компенсацией: call ! undo_call
        comp = None
        if " ! " in line:
            line, comp_text = line.split(" ! ", 1)
            comp = _parse_expr(comp_text, lineno, raw)
        steps.append(Step("expr", lineno, expr=_parse_expr(line, lineno, raw), compensation=comp,
                          retries=retries, effect=effect))
    if not steps:
        raise PickSyntaxError(0, "", "пустая программа")
    return steps
