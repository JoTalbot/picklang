"""П.3 — Статическая проверка IR до исполнения.

Ловит дёшево то, что иначе стоило бы цикл «запуск -> падение -> перепиши»:
неизвестные функции, неверную арность, использование переменных до привязки,
обращение к полям незаданных значений, отсутствие out, дубли цели.
Диагностики структурированы и, где возможно, несут готовый патч (см. fix.py).
Каждая несёт `level`: `error` — исполнять нельзя, `warning` — исполнять можно.
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
        for a in expr.get("args", []):
            yield from _expr_calls(a)
        return
    if "pipe" in expr:
        for item in expr.get("pipe") or []:
            yield from _expr_calls(item)
        return
    if "map" in expr:
        for _k, v in expr.get("map") or []:
            yield from _expr_calls(v)
        return
    # cmp и прочие составные выражения: спускаемся по значениям
    for v in expr.values():
        if isinstance(v, dict):
            yield from _expr_calls(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    yield from _expr_calls(item)
                elif isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict):
                    yield from _expr_calls(item[1])


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

    def scan(expr, line: int, injected: int = 0) -> None:
        for var in _expr_vars(expr):
            if var not in defined:
                diags.append({"line": line, "level": "error", "code": "UNDEFINED_VAR",
                              "message": f"переменная '{var}' используется до привязки"})
        if isinstance(expr, dict) and "pipe" in expr:
            # Пайплайн: результат левой части становится ПЕРВЫМ аргументом правой,
            # поэтому стадия после первой получает +1 к объявленной арности.
            for index, part in enumerate(expr.get("pipe") or []):
                scan(part, line, injected=1 if index else 0)
            return
        calls = list(_expr_calls(expr))
        if isinstance(expr, dict) and "call" in expr and calls and injected:
            calls[0] = (calls[0][0], calls[0][1] + injected)
        for name, argc in calls:
            if registry is not None and name not in registry:
                close = difflib.get_close_matches(name, list(registry), n=1)
                diags.append({"line": line, "level": "error", "code": "UNKNOWN_FN",
                              "message": f"функция '{name}' не зарегистрирована хостом",
                              "suggestion": close[0] if close else None})
            elif name in arities:
                lo, hi = arities[name]
                if not (lo <= argc <= hi):
                    diags.append({"line": line, "level": "error", "code": "ARITY",
                                  "message": f"'{name}' ждёт {lo}..{hi} аргументов, дано {argc}"})

    for step in ir.get("steps", []):
        line = step.get("line", 0)
        if step["kind"] == "goal":
            goals += 1
            defined.update(step.get("params", []))
            if goals > 1:
                diags.append({"line": line, "level": "error", "code": "DUP_GOAL",
                              "message": "цель > ... объявлена повторно"})
        # expr шага проверяется до привязки цели, компенсация — после:
        # она исполняется позже и законно ссылается на результат шага (p.tx).
        for key in ("expr", "fail"):
            if key in step:
                scan(step[key], line)
        if step["kind"] == "assign" and step.get("target"):
            defined.add(step["target"])
        if "compensation" in step:
            scan(step["compensation"], line)
        if step["kind"] == "parallel":
            # Ветви объявляют свои цели: a = f(x) || b = g(x) -> a и b определены.
            for target, branch_expr in step.get("branches", []):
                scan(branch_expr, line)
                if target:
                    defined.add(target)
        elif step["kind"] == "loop":
            if step.get("loop_var"):
                defined.add(step["loop_var"])
            body = step.get("body") or {}
            if body.get("expr") is not None:
                scan(body["expr"], line)
            if body.get("target"):
                defined.add(body["target"])
        elif step["kind"] == "donewhen":
            for cond in step.get("conditions") or []:
                scan(cond, line)
        elif step["kind"] == "wait":
            defined.add(step["signal"])
        elif step["kind"] == "out":
            has_out = True
    if not has_out:
        diags.append({"line": 0, "level": "error", "code": "NO_OUT",
                      "message": "в программе нет out — результат не определён"})
    return diags


def check_source(source: str, registry: dict | None = None) -> list[dict[str, Any]]:
    from .ir import compile_ir
    return check_ir(compile_ir(source), registry)


def check_contracts(source: str, contracts: dict[str, dict]) -> list[dict[str, Any]]:
    """П.7 — Контракты функций: поля результатов и арность по схемам хоста.

    contracts = {"bank.charge": {"args": ["user", "amt"], "returns": ["ok", "tx"]}}
    Ловит классическую ошибку слабых моделей: charge(...).track вместо .tx.
    """
    from .ir import compile_ir
    ir = compile_ir(source)
    diags: list[dict[str, Any]] = []
    produced_by: dict[str, str] = {}
    for step in ir["steps"]:
        expr = step.get("expr") or {}
        if step["kind"] == "assign" and isinstance(expr, dict) and "call" in expr:
            produced_by[step.get("target")] = expr["call"]
            spec = contracts.get(expr["call"])
            if spec and "args" in spec and len(expr.get("args", [])) != len(spec["args"]):
                diags.append({"line": step["line"], "level": "error", "code": "CONTRACT_ARITY",
                              "message": f"{expr['call']} ждёт аргументы {spec['args']}"})
        def walk(e):
            if isinstance(e, dict):
                if "get" in e:
                    root, _, field = e["get"].partition(".")
                    fn = produced_by.get(root)
                    spec = contracts.get(fn or "", {})
                    if fn and spec.get("returns") and field not in spec["returns"]:
                        diags.append({"line": step["line"], "level": "warning", "code": "UNKNOWN_FIELD",
                                      "message": f"у результата {fn} нет поля .{field}; есть: {spec['returns']}"})
                for v in e.values():
                    if isinstance(v, dict): walk(v)
                    elif isinstance(v, list):
                        for it in v:
                            if isinstance(it, dict): walk(it)
                            elif isinstance(it, list) and len(it) == 2: walk(it[1])
        for key in ("expr", "compensation"):
            if key in step: walk(step[key])
    return diags


# Эффект, выводимый из последнего сегмента имени вызова, когда ни контракт,
# ни аннотация @fx эффект не задают. Тот же набор глаголов, что
# DANGEROUS_SUFFIXES в proof.py, минус refund (это сама компенсация) и send
# (его риски покрывают taint-контроль и prove_safety, а не сага).
_INFERRED_EFFECTS = {"charge": "money", "pay": "money", "transfer": "money",
                     "delete": "irreversible", "remove": "irreversible", "drop": "irreversible"}


def _call_names_in_expr(expr):
    if isinstance(expr, tuple):
        if expr and expr[0] == "call":
            yield expr[1]
        for x in expr[1:]:
            yield from _call_names_in_expr(x)
    elif isinstance(expr, list):
        for x in expr:
            yield from _call_names_in_expr(x)


def _effect_steps(steps):
    """Шаги, у которых вообще могут быть эффект и компенсация, включая тело цикла."""
    for s in steps:
        if s.kind == "loop" and s.body is not None:
            yield from _effect_steps([s.body])
        elif s.kind in ("assign", "expr"):
            yield s


def check_effects(source: str, contracts: dict[str, dict] | None = None) -> list[dict[str, Any]]:
    """П.5 — Денежные/необратимые шаги без компенсации получают предупреждение.

    Эффект шага определяется по убыванию приоритета:
    1. поле effect в контракте функции (`{"bank.charge": {"effect": "money"}}`),
       если контракты переданы;
    2. аннотация `@fx` в исходнике (явная `@fx read` отключает вывод из имени);
    3. вывод из имени вызова, как в proof.prove_safety.
    Раньше работал только п.2: проверка зависела от того, что модель сама
    не забудет аннотировать опасный шаг, — а ловить она должна ровно такие
    пропуски. Шаги в теле `for` проверяются наравне с верхним уровнем.
    """
    from .parser import parse
    diags: list[dict[str, Any]] = []
    for s in _effect_steps(parse(source)):
        if s.compensation is not None:
            continue
        calls = list(_call_names_in_expr(s.expr)) if s.expr is not None else []
        effect: Any = None
        via = ""
        if contracts:
            for call in calls:
                spec = contracts.get(call) or {}
                if "effect" in spec:
                    effect, via = spec["effect"], f"эффект из контракта {call}"
                    break
        if effect is None and s.effect is not None:
            effect, via = s.effect, "эффект из аннотации @fx"
        if effect is None:
            for call in calls:
                inferred = _INFERRED_EFFECTS.get(call.rsplit(".", 1)[-1])
                if inferred:
                    effect = inferred
                    via = f"эффект выведен из имени {call}; укажите @fx, чтобы переопределить"
                    break
        if effect in ("money", "irreversible"):
            diags.append({"line": s.line, "level": "warning", "code": "EFFECT_NO_COMPENSATION",
                          "message": f"шаг с эффектом {effect} без компенсации ! — откат нечем делать ({via})"})
    return diags

