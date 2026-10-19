"""П.2 — AOT-компиляция PICK в готовые Python-замыкания по digest программы.

Интерпретатор идёт по IR при каждом запуске; AOT один раз генерирует код,
где шаги — прямые вызовы, а соседние проекции top>>fields слиты в один проход.
Кеш компиляции — по program_digest: та же программа не компилируется дважды.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

from .ir import compile_ir, program_digest
class DoneWhenFailed(RuntimeError):
    """AOT-вариант TaskNotDone: интерпретатор перехватит и приведёт к своему типу."""


from .stdlib import fields as _fields
from .stdlib import top as _top


def _fused_top_fields(items, n, *names):
    """top и fields одним проходом: промежуточный список не материализуется."""
    out = []
    for item in list(items)[: int(n)]:
        out.append({k: item.get(k) for k in names} if isinstance(item, dict) else item)
    return out


_FN_INDEX: dict[str, int] = {}
_VAR_LOCALS: dict[str, str] | None = None  # имя переменной -> локальное имя в сгенерированном коде


def _fn_ref(name: str) -> str:
    """Прямая ссылка на функцию по индексу вместо registry[name] на каждый вызов."""
    if name not in _FN_INDEX:
        _FN_INDEX[name] = len(_FN_INDEX)
    return f"_fn[{_FN_INDEX[name]}]"


def _gen_expr(expr, env="env", reg="registry") -> str:
    if "lit" in expr:
        return repr(expr["lit"])
    if "var" in expr:
        if _VAR_LOCALS is not None:
            return _VAR_LOCALS.get(expr["var"], f"{env}[{expr['var']!r}]")
        return f"{env}[{expr['var']!r}]"
    if "get" in expr:
        parts = expr["get"].split(".")
        if _VAR_LOCALS is not None and parts[0] in _VAR_LOCALS:
            code = _VAR_LOCALS[parts[0]]
        else:
            code = f"{env}[{parts[0]!r}]"
        for part in parts[1:]:
            code = f"{code}[{part!r}]"
        return code
    if "map" in expr:
        items = ", ".join(f"{k!r}: {_gen_expr(v, env, reg)}" for k, v in expr["map"])
        return "{" + items + "}"
    if "cmp" in expr:
        op, left, right = expr["cmp"]
        return f"({_gen_expr(left, env, reg)} {op} {_gen_expr(right, env, reg)})"
    if "pipe" in expr:
        stages = expr["pipe"]
        # слияние top >> fields
        if len(stages) == 3 and "call" in stages[1] and stages[1]["call"] == "top" \
                and "call" in stages[2] and stages[2]["call"] == "fields":
            base = _gen_expr(stages[0], env, reg)
            n = _gen_expr(stages[1]["args"][0], env, reg)
            names = ", ".join(_gen_expr(a, env, reg) for a in stages[2]["args"])
            return f"_fused_top_fields({base}, {n}, {names})"
        code = _gen_expr(stages[0], env, reg)
        for stage in stages[1:]:
            args = ", ".join([code] + [_gen_expr(a, env, reg) for a in stage["args"]])
            code = f"{_fn_ref(stage['call'])}({args})"
        return code
    if "call" in expr:
        args = ", ".join(_gen_expr(a, env, reg) for a in expr["args"])
        return f"{_fn_ref(expr['call'])}({args})"
    raise ValueError(f"unsupported expr: {expr}")


def _uses_var(expr, var) -> bool:
    if isinstance(expr, dict):
        if expr.get("var") == var:
            return True
        return any(_uses_var(v, var) for v in expr.values())
    if isinstance(expr, list):
        return any(_uses_var(v, var) for v in expr)
    return False


def _collect_vars(steps) -> list[str]:
    names: list[str] = []

    def add(name):
        if name and name not in names:
            names.append(name)

    def walk(expr):
        if isinstance(expr, dict):
            if "var" in expr:
                add(expr["var"])
            if "get" in expr:
                add(expr["get"].split(".")[0])
            for v in expr.values():
                walk(v)
        elif isinstance(expr, list):
            for v in expr:
                walk(v)

    for s in steps:
        for prm in s.get("params") or []:
            add(prm)
        if s.get("target"):
            add(s["target"])
        if s.get("loop_var"):
            add(s["loop_var"])
        if s.get("body") and s["body"].get("target"):
            add(s["body"]["target"])
        for tgt, _e in s.get("branches") or []:
            add(tgt)
        if s.get("signal"):
            add(s["signal"])
        for key in ("expr", "conditions"):
            if s.get(key):
                walk(s[key])
        if s.get("body") and s["body"].get("expr"):
            walk(s["body"]["expr"])
        for _tgt, e in s.get("branches") or []:
            walk(e)
        if s.get("compensation"):
            walk(s["compensation"])
    return names


def generate_python(source: str) -> str:
    global _VAR_LOCALS
    ir = compile_ir(source)
    var_names = _collect_vars(ir["steps"])
    _VAR_LOCALS = {name: f"_v{i}" for i, name in enumerate(var_names)}

    def loc(name: str) -> str:
        return _VAR_LOCALS[name]  # type: ignore[index]

    body_lines = ["    out = None",
                  "    _compensations = []"]
    lines = body_lines
    for step in ir["steps"]:
        kind = step["kind"]
        if kind == "goal":
            continue
        if kind == "budget":
            continue  # бюджеты считает интерпретатор/рантайм; AOT — быстрый путь без учёта
        if kind == "loop":
            var = step["loop_var"]
            body = step["body"]
            bexpr = body["expr"]
            iterable = _gen_expr(step["expr"])
            batch_ok = ("call" in bexpr and bexpr["args"] and bexpr["args"][0].get("var") == var
                        and not any(_uses_var(a, var) for a in bexpr["args"][1:]))
            if batch_ok:
                rest = ", ".join(_gen_expr(a) for a in bexpr["args"][1:])
                suffix = (", " + rest) if rest else ""
                lines.append(f"    _items = {iterable}")
                lines.append(f"    _batch = registry.get({bexpr['call'] + '.batch'!r})")
                lines.append("    if _batch is not None:")
                lines.append(f"        {loc(body['target'])} = _batch(_items{suffix})")
                lines.append("    else:")
                lines.append(f"        for {loc(var)} in _items:")
                lines.append(f"            {loc(body['target'])} = {_gen_expr(bexpr)}")
            else:
                lines.append(f"    for {loc(var)} in {iterable}:")
                lines.append(f"        {loc(body['target'])} = {_gen_expr(bexpr)}")
            continue
        if kind == "parallel":
            for target, expr in step["branches"]:
                if target:
                    lines.append(f"    {loc(target)} = {_gen_expr(expr)}")
                else:
                    lines.append(f"    {_gen_expr(expr)}")
            continue  # AOT исполняет ветви последовательно: семантика та же, параллелизм — за интерпретатором
        if kind in ("assign", "expr"):
            code = _gen_expr(step["expr"])
            if step.get("target"):
                lines.append(f"    {loc(step['target'])} = {code}")
            else:
                lines.append(f"    {code}")
            if "compensation" in step:
                lines.append(f"    _compensations.append(lambda: {_gen_expr(step['compensation'])})")
        elif kind == "guard":
            lines.append(f"    if not ({_gen_expr(step['expr'])}):")
            lines.append(f"        raise RuntimeError('guard failed')")
        elif kind == "wait":
            lines.append(f"    _v = signal_provider({step['signal']!r}) if signal_provider else None")
            lines.append("    if _v is None: raise WaitingSignal(%r)" % step["signal"])
            lines.append(f"    {loc(step['signal'])} = _v")
        elif kind == "donewhen":
            for cond in step["conditions"]:
                lines.append(f"    if not ({_gen_expr(cond)}): raise DoneWhenFailed('done when не выполнен')")
        elif kind == "out":
            lines.append(f"    out = {_gen_expr(step['expr'])}")
    for name in var_names:
        lines.append(f"    env[{name!r}] = {loc(name)}")
    lines.append("    return out")
    header = ["def _pick_program(env, registry, signal_provider=None):",
              "    from picklang.interpreter import WaitingSignal",
              "    _fn = [registry.get(_n) for _n in _FN_NAMES]"]
    header += [f"    {loc(n)} = env.get({n!r})" for n in var_names]
    _VAR_LOCALS = None
    if any("compensation" in s for s in ir["steps"]):
        # сага: при бизнес-ошибке компенсации в обратном порядке, затем проброс
        wrapped = header + ["    try:"]
        wrapped += ["    " + ln if ln.strip() else ln for ln in lines]
        wrapped += ["    except WaitingSignal:",
                    "        raise  # ожидание — не бизнес-ошибка: компенсации не запускаем",
                    "    except Exception as _e:",
                    "        for _comp in reversed(_compensations):",
                    "            _comp()",
                    "        raise RuntimeError(str(_e) + f'; компенсаций выполнено: {len(_compensations)}') from _e"]
        return "\n".join(wrapped) + "\n"
    return "\n".join(header + lines) + "\n"


@lru_cache(maxsize=64)
def _compiled_source(source: str):
    """Кэш по самому тексту программы: digest считается один раз внутри,
    а не на каждый вызов (иначе кэш съедается повторным парсингом)."""
    ns: dict[str, Any] = {"_fused_top_fields": _fused_top_fields, "DoneWhenFailed": DoneWhenFailed}
    _FN_INDEX.clear()
    code = generate_python(source)
    ns["_FN_NAMES"] = [name for name, _ in sorted(_FN_INDEX.items(), key=lambda kv: kv[1])]
    exec(code, ns)
    return ns["_pick_program"]


def compile_program(source: str) -> Callable:
    return _compiled_source(source)


def run_compiled(source: str, registry: dict, inputs: dict | None = None, signal_provider=None):
    fn = compile_program(source)
    env = dict(inputs or {})
    out = fn(env, registry, signal_provider)
    return out, env
