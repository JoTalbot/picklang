"""П.3 — Статическая проверка IR до исполнения.

Ловит дёшево то, что иначе стоило бы цикл «запуск -> падение -> перепиши»:
неизвестные функции, неверную арность, использование переменных до привязки,
обращение к полям незаданных значений, отсутствие out, дубли цели.
Диагностики структурированы и, где возможно, несут готовый патч (см. fix.py).
"""
from __future__ import annotations

import difflib
import inspect
from typing import Any


def _sig_arity(fn) -> tuple[int, int] | None:
    try:
        params = [p for p in inspect.signature(fn).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        variadic = any(p.kind == p.VAR_POSITIONAL for p in inspect.signature(fn).parameters.values())
        required = sum(1 for p in params if p.default is p.empty)
        return required, 99 if variadic else len(params)
    except (TypeError, ValueError):
        return None


def _expr_vars(expr):
    if not isinstance(expr, dict):
        return
    if "var" in expr:
        yield expr["var"]
    if "get" in expr:
        yield expr["get"].split(".")[0]
    for v in expr.values():
        if isinstance(v, dict):
            yield from _expr_vars(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, list):
                    yield from _expr_vars(item[1]) if len(item) == 2 and isinstance(item[1], dict) else iter(())
                elif isinstance(item, dict):
                    yield from _expr_vars(item)


def _expr_calls(expr):
    if not isinstance(expr, dict):
        return
    if "call" in expr:
        yield expr["call"], len(expr.get("args", []))
        for a in expr["args"]:
            yield from _expr_calls(a)
    for key in ("pipe",):
        for item in expr.get(key, []) if isinstance(expr.get(key), list) else []:
            yield from _expr_calls(item)
    if "map" in expr:
        for _k, v in expr["map"]:
            yield from _expr_calls(v)


def check_ir(ir: dict[str, Any], registry: dict | None = None) -> list[dict[str, Any]]:
    diags: list[dict[str, Any]] = []
    defined: set[str] = set()
    has_out = False
    goals = 0
    arities = {}
    if registry:
        for name, fn in registry.items():
            ar = _sig_arity(fn) if callable(fn) else fn
            if ar:
                arities[name] = ar
    for step in ir.get("steps", []):
        line = step.get("line", 0)
        if step["kind"] == "goal":
            goals += 1
            defined.update(step.get("params", []))
            if goals > 1:
                diags.append({"line": line, "code": "DUP_GOAL", "message": "цель > ... объявлена повторно"})
        # expr шага проверяется до привязки цели, компенсация — после:
        # она исполняется позже и законно ссылается на результат шага (p.tx).
        exprs = [step[k] for k in ("expr", "fail") if k in step]
        if step["kind"] == "assign" and step.get("target"):
            # цель станет определена для компенсации и следующих шагов
            pass
        for expr in exprs:
            for var in _expr_vars(expr):
                if var not in defined:
                    diags.append({"line": line, "code": "UNDEFINED_VAR",
                                  "message": f"переменная '{var}' используется до привязки"})
            for name, argc in _expr_calls(expr):
                if registry is not None and name not in registry:
                    close = difflib.get_close_matches(name, list(registry), n=1)
                    diags.append({"line": line, "code": "UNKNOWN_FN",
                                  "message": f"функция '{name}' не зарегистрирована хостом",
                                  "suggestion": close[0] if close else None})
                elif name in arities:
                    lo, hi = arities[name]
                    if not (lo <= argc <= hi):
                        diags.append({"line": line, "code": "ARITY",
                                      "message": f"'{name}' ждёт {lo}..{hi} аргументов, дано {argc}"})
        if step["kind"] == "assign" and step.get("target"):
            defined.add(step["target"])
        if "compensation" in step:
            for var in _expr_vars(step["compensation"]):
                if var not in defined:
                    diags.append({"line": line, "code": "UNDEFINED_VAR",
                                  "message": f"переменная '{var}' используется до привязки"})
            for name, argc in _expr_calls(step["compensation"]):
                if registry is not None and name not in registry:
                    close = difflib.get_close_matches(name, list(registry), n=1)
                    diags.append({"line": line, "code": "UNKNOWN_FN",
                                  "message": f"функция '{name}' не зарегистрирована хостом",
                                  "suggestion": close[0] if close else None})
        if step["kind"] == "wait":
            defined.add(step["signal"])
        if step["kind"] == "out":
            has_out = True
    if not has_out:
        diags.append({"line": 0, "code": "NO_OUT", "message": "в программе нет out — результат не определён"})
    return diags


def check_source(source: str, registry: dict | None = None) -> list[dict[str, Any]]:
    from .ir import compile_ir
    return check_ir(compile_ir(source), registry)
