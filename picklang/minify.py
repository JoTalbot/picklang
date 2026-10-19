"""П.5 — Сжатие идентификаторов с source-map.

Токенизаторы режут длинные имена на куски, поэтому `payment_result` дороже,
чем `b`. compress() переименовывает переменные программы в a, b, c... и
возвращает карту; expand() восстанавливает читаемый вид. Имена функций хоста
не трогаем: это словарь проекта, а не переменные. Строки не трогаем вообще.
"""
from __future__ import annotations

import re

from .ir import compile_ir, program_digest

_TOKEN = re.compile(r'("[^"]*"|\'[^\']*\')|([A-Za-z_]\w*)')


def _collect_vars(source: str) -> list[str]:
    ir = compile_ir(source)
    names: list[str] = []

    def add(n):
        if n and n not in names:
            names.append(n)

    def walk(expr):
        if not isinstance(expr, dict):
            return
        if "var" in expr: add(expr["var"])
        if "get" in expr: add(expr["get"].split(".")[0])
        for v in expr.values():
            if isinstance(v, dict): walk(v)
            elif isinstance(v, list):
                for it in v:
                    if isinstance(it, dict): walk(it)
                    elif isinstance(it, list) and len(it) == 2: walk(it[1])

    for step in ir["steps"]:
        for p in step.get("params", []): add(p)
        if step.get("target"): add(step["target"])
        if step.get("signal"): add(step["signal"])
        for k in ("expr", "compensation", "fail"):
            if k in step: walk(step[k])
    return names


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
        if m.group(2) and m.start() > 0 and source[m.start() - 1] == ".":
            out.append(m.group(2))  # поле .tx, .ok, функция mod.fn — не переменная
        else:
            out.append(repl(m))
        pos = m.end()
    out.append(source[pos:])
    return "".join(out)


def compress(source: str) -> tuple[str, dict[str, str]]:
    names = _collect_vars(source)
    mapping = {name: _short(i) for i, name in enumerate(names)}
    return _rewrite(source, mapping), mapping


def expand(minified: str, mapping: dict[str, str]) -> str:
    inverse = {v: k for k, v in mapping.items()}
    return _rewrite(minified, inverse)


def equivalent(a: str, b: str) -> bool:
    """Две программы эквивалентны с точностью до имён переменных."""
    return compile_ir(a) == compile_ir(b)
