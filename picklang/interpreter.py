"""Исполнитель PICK поверх реестра функций хоста.

Каждый вызов mod.fn(...) может проходить через activity_hook — точку, где
durable_weave подменяет «просто вызов» на «вызов с коммитом в журнал+CAS,
который при resume не повторится». Язык при этом ничего не знает про хранение.
"""
from __future__ import annotations

from typing import Any, Callable

from .parser import parse

Registry = dict[str, Callable[..., Any]]


class PickRuntimeError(Exception):
    pass


class WaitingSignal(Exception):
    def __init__(self, signal: str):
        super().__init__(signal)
        self.signal = signal


def _eval(expr, env, registry, hook, trace):
    kind = expr[0]
    if kind == "lit":
        return expr[1]
    if kind == "var":
        if expr[1] not in env:
            raise PickRuntimeError(f"переменная не определена: {expr[1]}")
        return env[expr[1]]
    if kind == "get":
        parts = expr[1].split(".")
        value = env.get(parts[0])
        if value is None and parts[0] not in env:
            raise PickRuntimeError(f"переменная не определена: {parts[0]}")
        for part in parts[1:]:
            value = value[part] if isinstance(value, dict) else getattr(value, part)
        return value
    if kind == "map":
        return {k: _eval(v, env, registry, hook, trace) for k, v in expr[1]}
    if kind == "pipe":
        value = _eval(expr[1][0], env, registry, hook, trace)
        for stage in expr[1][1:]:
            if stage[0] != "call":
                raise PickRuntimeError("в пайплайне после >> ожидается вызов")
            value = _call(stage, env, registry, hook, trace, piped=value)
        return value
    if kind == "call":
        return _call(expr, env, registry, hook, trace)
    raise PickRuntimeError(f"unknown expr {kind}")


def _call(expr, env, registry, hook, trace, piped=...):
    _, name, arg_exprs = expr
    args = [_eval(a, env, registry, hook, trace) for a in arg_exprs]
    if piped is not ...:
        args = [piped] + args
    fn = registry.get(name)
    if fn is None:
        raise PickRuntimeError(f"функция не зарегистрирована хостом: {name}")
    trace.append(name)
    if hook is not None:
        return hook(name, fn, args)
    return fn(*args)


def run_program(source: str, registry: Registry, inputs: dict[str, Any] | None = None,
                signal_provider: Callable[[str], Any] | None = None,
                activity_hook: Callable[[str, Callable, list], Any] | None = None):
    """Возвращает (output, env, trace). Бросает WaitingSignal, если сигнала ещё нет."""
    steps = parse(source)
    env: dict[str, Any] = dict(inputs or {})
    compensations: list = []
    trace: list[str] = []
    output = None
    try:
        for step in steps:
            if step.kind == "goal":
                missing = [p for p in step.params if p not in env]
                if missing:
                    raise PickRuntimeError(f"не переданы параметры цели: {', '.join(missing)}")
            elif step.kind in ("assign", "expr"):
                value = _eval(step.expr, env, registry, activity_hook, trace)
                if step.target:
                    env[step.target] = value
                if step.compensation is not None:
                    compensations.append(step.compensation)
            elif step.kind == "guard":
                ok = _eval(step.expr, env, registry, activity_hook, trace)
                if not ok:
                    reason = _eval(step.target, env, registry, activity_hook, trace)
                    raise PickRuntimeError(f"guard failed: {reason}")
            elif step.kind == "wait":
                value = signal_provider(step.signal) if signal_provider else None
                if value is None:
                    raise WaitingSignal(step.signal)
                env[step.signal] = value
            elif step.kind == "out":
                output = _eval(step.expr, env, registry, activity_hook, trace)
    except (PickRuntimeError, WaitingSignal):
        # Сага: компенсации в обратном порядке; WaitingSignal компенсаций НЕ запускает —
        # процесс не упал, а встал на паузу.
        raise
    except Exception as exc:
        comp_trace = []
        for comp in reversed(compensations):
            try:
                _eval(comp, env, registry, activity_hook, comp_trace)
            except Exception:
                pass  # компенсация обязана быть идемпотентной на стороне хоста
        raise PickRuntimeError(f"выполнение сорвалось, компенсаций выполнено: {len(comp_trace)}; причина: {exc}") from exc
    return output, env, trace
