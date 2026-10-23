"""П.5 — Сжатие идентификаторов с source-map.

Токенизаторы режут длинные имена на куски, поэтому `payment_result` дороже,
чем `b`. compress() переименовывает переменные программы в a, b, c... и
возвращает карту; expand() восстанавливает читаемый вид. Имена функций хоста
не трогаем: это словарь проекта, а не переменные. Строки не трогаем вообще.
"""
from __future__ import annotations

import re

from .ir import compile_ir, program_digest

# Строки с escape-последовательностями и комментарии — непрозрачные токены.
_TOKEN = re.compile(r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\#[^\n]*)|([A-Za-z_]\w*)")
_RESERVED = {"approval", "true", "false", "null", "True", "False", "None",
             "and", "or", "not", "out", "for", "in", "done", "when", "fail",
             "if", "else_if", "else", "end", "wait", "budget", "fx"}


def _collect_vars(source: str) -> list[str]:
    ir = compile_ir(source)
    names: list[str] = []
    signals: set[str] = set()

    def add(n):
        if n and n not in _RESERVED and n not in names:
            names.append(n)

    def walk(expr):
        if isinstance(expr, dict):
            if "lit" in expr:
                return  # содержимое литерала — данные, не узлы IR
            if "var" in expr: add(expr["var"])
            if "get" in expr: add(expr["get"].split(".")[0])
            for value in expr.values():
                walk(value)
        elif isinstance(expr, list):
            for value in expr:
                walk(value)

    def steps(items):
        for step in items:
            if step.get("signal"):
                signals.add(step["signal"])
            for param in step.get("params", []): add(param)
            for key in ("target", "signal", "loop_var"):
                add(step.get(key))
            for key in ("expr", "compensation", "fail", "conditions"):
                walk(step.get(key))
            for target, expr in step.get("branches", []):
                add(target)
                walk(expr)
            if step.get("body"):
                steps([step["body"]])
            if step.get("else_step"):
                steps([step["else_step"]])
            for case in step.get("cases", []):
                walk(case["condition"])
                steps(case["steps"])
    steps(ir["steps"])
    return [name for name in names if name not in signals]


def _short(i: int) -> str:
    letters = "abcdefghijklmnopqrstuvwxyz"
    s = letters[i % 26]
    return s if i < 26 else s + str(i // 26)


def _rewrite(source: str, mapping: dict[str, str]) -> str:
    def repl(m):
        if m.group(1) is not None:
            return m.group(1)  # строка — не трогаем
        name = m.group(2)
        return mapping.get(name, name)
    # Не переименовываем имя после точки (это поле/функция хоста) и перед '(' обрабатываем естественно:
    out = []
    pos = 0
    for m in _TOKEN.finditer(source):
        out.append(source[pos:m.start()])
        suffix = source[m.end():]
        is_call = re.match(r"(?:\.[A-Za-z_]\w*)*\s*\(", suffix)
        is_key = re.search(r"(?:\{|,)\s*$", source[:m.start()]) and re.match(r"\s*:", suffix)
        if m.group(2) and (is_call or is_key or (m.start() > 0 and source[m.start() - 1] == ".")):
            out.append(m.group(2))  # поле .tx, .ok, функция mod.fn — не переменная
        else:
            out.append(repl(m))
        pos = m.end()
    out.append(source[pos:])
    return "".join(out)


def compress(source: str) -> tuple[str, dict[str, str]]:
    names = _collect_vars(source)
    # Не захватываем уже существующие имена: это важно и для expand().
    unavailable = _RESERVED | {m.group(2) for m in _TOKEN.finditer(source) if m.group(2)}
    mapping = {}
    index = 0
    for name in names:
        while _short(index) in unavailable:
            index += 1
        mapping[name] = _short(index)
        unavailable.add(_short(index))
        index += 1
    return _rewrite(source, mapping), mapping


def expand(minified: str, mapping: dict[str, str]) -> str:
    inverse = {v: k for k, v in mapping.items()}
    return _rewrite(minified, inverse)


def equivalent(a: str, b: str) -> bool:
    """Две программы эквивалентны с точностью до имён переменных."""
    return compile_ir(a) == compile_ir(b)
