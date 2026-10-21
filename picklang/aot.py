"""П.2 — AOT-компиляция PICK в готовые Python-замыкания по digest программы.

Интерпретатор идёт по IR при каждом запуске; AOT один раз генерирует код,
где шаги — прямые вызовы, а соседние проекции top>>fields слиты в один проход.
Кеш компиляции — по program_digest: та же программа не компилируется дважды.
"""
from __future__ import annotations

from functools import lru_cache
from threading import RLock
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


class _LanguageError(RuntimeError):
    """Ошибка семантики PICK в сгенерированном коде (не ошибка функции хоста)."""


_UNDEFINED = object()


def _defined(value, name):
    if value is _UNDEFINED:
        raise _LanguageError(f"переменная не определена: {name}")
    return value


def _field(value, part):
    return value[part] if isinstance(value, dict) else getattr(value, part)


def _run_compensations(compensations):
    for comp in reversed(compensations):
        try:
            comp()
        except Exception:
            pass


def _checked_fn(name, fn):
    if fn is not None:
        return fn

    def _missing(*args):
        raise _LanguageError(f"функция не зарегистрирована хостом: {name}")

    return _missing


_COMPILE_LOCK = RLock()  # генератор использует временное глобальное состояние
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
        name = expr["var"]
        if _VAR_LOCALS is not None and name in _VAR_LOCALS:
            return f"_defined({_VAR_LOCALS[name]}, {name!r})"
        return f"_defined({env}.get({name!r}, _UNDEFINED), {name!r})"
    if "get" in expr:
        parts = expr["get"].split(".")
        if _VAR_LOCALS is not None and parts[0] in _VAR_LOCALS:
            code = f"_defined({_VAR_LOCALS[parts[0]]}, {parts[0]!r})"
        else:
            code = f"_defined({env}.get({parts[0]!r}, _UNDEFINED), {parts[0]!r})"
        for part in parts[1:]:
            code = f"_field({code}, {part!r})"
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


def _contains_fused_top_fields(node) -> bool:
    if isinstance(node, dict):
        stages = node.get("pipe")
        if (isinstance(stages, list) and len(stages) == 3
                and isinstance(stages[1], dict) and stages[1].get("call") == "top"
                and isinstance(stages[2], dict) and stages[2].get("call") == "fields"):
            return True
        return any(_contains_fused_top_fields(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_fused_top_fields(v) for v in node)
    return False


@lru_cache(maxsize=128)
def _source_has_fused_top_fields(source: str) -> bool:
    return _contains_fused_top_fields(compile_ir(source))


def _registry_supports_fused_pipes(source: str, registry: dict) -> bool:
    """Слияние top>>fields повторяет семантику stdlib, а не произвольного реестра."""
    if not _source_has_fused_top_fields(source):
        return True
    return registry.get("top") is _top and registry.get("fields") is _fields


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
        if s.get("body") and s["body"].get("compensation"):
            walk(s["body"]["compensation"])
        if s.get("fail"):
            walk(s["fail"])
        for _tgt, e in s.get("branches") or []:
            walk(e)
        if s.get("compensation"):
            walk(s["compensation"])
    return names


def _generate_python_unlocked(source: str) -> str:
    global _VAR_LOCALS
    ir = compile_ir(source)
    unsupported = sorted({step["kind"] for step in ir["steps"] if step["kind"] == "budget"})
    if unsupported:
        kinds = ", ".join(unsupported)
        raise ValueError(f"AOT does not support {kinds}; use the interpreter via run_program()")
    unsupported_features = set()
    for step in ir["steps"]:
        if step.get("retries") or step.get("effect"):
            unsupported_features.add("retries/effects")
        if step["kind"] == "loop":
            body = step.get("body") or {}
            if (body.get("kind") not in ("assign", "expr") or body.get("retries")
                    or body.get("effect") or "compensation" in body):
                unsupported_features.add("unsupported loop body")
        if step["kind"] == "wait" and ("timeout" in step or "else_step" in step):
            unsupported_features.add("wait timeout/else")
    if unsupported_features:
        features = ", ".join(sorted(unsupported_features))
        raise ValueError(f"AOT does not support {features}; use the interpreter via run_program()")
    var_names = _collect_vars(ir["steps"])
    _VAR_LOCALS = {name: f"_v{i}" for i, name in enumerate(var_names)}

    def loc(name: str) -> str:
        return _VAR_LOCALS[name]  # type: ignore[index]

    body_lines = ["    out = None",
                  "    _compensations = []"]
    lines = body_lines
    done_conditions: list = []
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
            batch_ok = (body.get("kind") == "assign" and body.get("target")
                        and "call" in bexpr and bexpr["args"] and bexpr["args"][0].get("var") == var
                        and not any(_uses_var(a, var) for a in bexpr["args"][1:]))

            body_code = _gen_expr(bexpr)
            if body.get("target"):
                body_code = f"{loc(body['target'])} = {body_code}"

            if batch_ok:
                rest = ", ".join(_gen_expr(a) for a in bexpr["args"][1:])
                suffix = (", " + rest) if rest else ""
                lines.append(f"    _items = {iterable}")
                lines.append(f"    _batch = registry.get({bexpr['call'] + '.batch'!r})")
                lines.append("    if _batch is not None:")
                lines.append("        _items = list(_items)")
                lines.append(f"        {loc(body['target'])} = _batch(_items{suffix})")
                lines.append(f"        {loc(var)} = _items[-1] if _items else None")
                lines.append("    else:")
                lines.append(f"        for {loc(var)} in _items:")
                lines.append(f"            {body_code}")
            else:
                lines.append(f"    for {loc(var)} in {iterable}:")
                lines.append(f"        {body_code}")
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
            reason = _gen_expr(step["fail"]) if step.get("fail") else "''"
            lines.append(f"        raise _LanguageError('guard failed: ' + str({reason}))")
        elif kind == "wait":
            lines.append(f"    _v = signal_provider({step['signal']!r}) if signal_provider else None")
            lines.append("    if _v is None: raise WaitingSignal(%r)" % step["signal"])
            lines.append(f"    {loc(step['signal'])} = _v")
        elif kind == "donewhen":
            done_conditions = step["conditions"]
        elif kind == "out":
            lines.append(f"    out = {_gen_expr(step['expr'])}")
    for cond in done_conditions:
        lines.append(f"    if not ({_gen_expr(cond)}): raise DoneWhenFailed('критерии готовности (done when) не выполнены')")
    for name in var_names:
        lines.append(f"    if {loc(name)} is not _UNDEFINED: env[{name!r}] = {loc(name)}")
    lines.append("    return out")
    header = ["def _pick_program(env, registry, signal_provider=None):",
              "    from picklang.interpreter import AbortExecution, WaitingSignal",
              "    _fn = [_checked_fn(_n, registry.get(_n)) for _n in _FN_NAMES]"]
    goal_params = next((s.get("params") or [] for s in ir["steps"] if s["kind"] == "goal"), [])
    if goal_params:
        header.append(f"    _missing_params = [name for name in {tuple(goal_params)!r} if name not in env]")
        header.append("    if _missing_params: raise _LanguageError('не переданы параметры цели: ' + ', '.join(_missing_params))")
    header += [f"    {loc(n)} = env.get({n!r}, _UNDEFINED)" for n in var_names]
    _VAR_LOCALS = None
    if any("compensation" in s for s in ir["steps"]):
        # сага: при бизнес-ошибке компенсации в обратном порядке, затем проброс
        wrapped = header + ["    try:"]
        wrapped += ["    " + ln if ln.strip() else ln for ln in lines]
        wrapped += ["    except (WaitingSignal, AbortExecution):",
                    "        raise  # ожидание/аборт — не бизнес-ошибка: компенсации не запускаем",
                    "    except (DoneWhenFailed, _LanguageError):",
                    "        _run_compensations(_compensations)",
                    "        raise",
                    "    except Exception as _e:",
                    "        _run_compensations(_compensations)",
                    "        raise _LanguageError(f'выполнение сорвалось, компенсаций выполнено: {len(_compensations)}; причина: {_e}') from _e"]
        return "\n".join(wrapped) + "\n"
    return "\n".join(header + lines) + "\n"


def generate_python(source: str) -> str:
    """Generate code atomically; never leak compiler globals between threads."""
    global _VAR_LOCALS
    with _COMPILE_LOCK:
        try:
            return _generate_python_unlocked(source)
        finally:
            _VAR_LOCALS = None


@lru_cache(maxsize=64)
def _compiled_source(source: str):
    """Кэш по тексту; сборка кода и индекса функций атомарны между потоками."""
    with _COMPILE_LOCK:
        ns: dict[str, Any] = {
            "_fused_top_fields": _fused_top_fields,
            "DoneWhenFailed": DoneWhenFailed,
            "_LanguageError": _LanguageError,
            "_UNDEFINED": _UNDEFINED,
            "_defined": _defined,
            "_field": _field,
            "_run_compensations": _run_compensations,
            "_checked_fn": _checked_fn,
        }
        _FN_INDEX.clear()
        code = generate_python(source)
        ns["_FN_NAMES"] = [name for name, _ in sorted(_FN_INDEX.items(), key=lambda kv: kv[1])]
        exec(code, ns)
        return ns["_pick_program"]


def compile_program(source: str) -> Callable:
    return _compiled_source(source)


def _run_compiled_unchecked(source: str, registry: dict, inputs: dict | None = None, signal_provider=None):
    """Execution path for run_program, which checks the *original* registry
    before wrapping it for tracing (wrappers would defeat identity checks)."""
    fn = compile_program(source)
    env = dict(inputs or {})
    out = fn(env, registry, signal_provider)
    return out, env


def run_compiled(source: str, registry: dict, inputs: dict | None = None, signal_provider=None):
    if not _registry_supports_fused_pipes(source, registry):
        raise ValueError("AOT fused top>>fields requires stdlib top/fields; use the interpreter via run_program()")
    return _run_compiled_unchecked(source, registry, inputs, signal_provider)
