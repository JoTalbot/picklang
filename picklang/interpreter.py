"""Исполнитель PICK поверх реестра функций хоста.

Агентные возможности второй волны:
- !budget steps<N, tokens<M: рантайм сам следит за расходом и останавливает цикл;
- done when: ...: критерии готовности проверяются до объявления результата;
- a = f() || b = g(): параллельные ветви (субагентные по смыслу), слияние по порядку;
- capabilities: программа видит только разрешённые ей функции реестра;
- secret:NAME в аргументах подставляется рантаймом из окружения и не попадает
  ни в программу, ни в контекст модели;
- untrusted={...}: результаты помеченных инструментов оборачиваются как данные,
  а не инструкции: {"data": ..., "untrusted": True}.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from functools import lru_cache

from .parser import parse
from .result_guard import estimate_tokens, estimate_tokens_fast


@lru_cache(maxsize=128)
def _parse_cached(source: str):
    """Программа парсится один раз по тексту (digest — сам текст в ключе lru_cache)."""
    return parse(source)

Registry = dict[str, Callable[..., Any]]


class PickRuntimeError(Exception):
    pass


class BudgetExceeded(PickRuntimeError):
    pass


class TaskNotDone(PickRuntimeError):
    pass


class AbortExecution(Exception):
    """Прерывание без компенсаций: «процесс умер», а не «программа ошиблась»."""


class WaitingSignal(Exception):
    def __init__(self, signal: str):
        super().__init__(signal)
        self.signal = signal


class _State:
    def __init__(self, capabilities=None, untrusted=(), resolve_secrets=True):
        self.steps = 0
        self.tokens = 0
        self.exact_tokens = False  # True только если в программе есть !budget tokens
        self.budget: dict[str, int] = {}
        self.capabilities = capabilities
        self.untrusted = set(untrusted)
        self.resolve_secrets = resolve_secrets
        self.lock = threading.Lock()
        self.approved = False
        self.effects: list[str] = []
        self.tainted: set[str] = set()

    def charge_call(self, name: str, result: Any) -> Any:
        with self.lock:
            self.steps += 1
            self.tokens += estimate_tokens(result) if self.exact_tokens else estimate_tokens_fast(result)
            if "steps" in self.budget and self.steps > self.budget["steps"]:
                raise BudgetExceeded(f"бюджет шагов исчерпан на {name}: {self.steps} > {self.budget['steps']}")
            if "tokens" in self.budget and self.tokens > self.budget["tokens"]:
                raise BudgetExceeded(f"бюджет токенов результатов исчерпан на {name}: {self.tokens} > {self.budget['tokens']}")
        if name in self.untrusted:
            return {"data": result, "untrusted": True}
        return result


def _resolve_secrets(value):
    if isinstance(value, str) and value.startswith("secret:"):
        name = value[len("secret:"):]
        if name not in os.environ:
            raise PickRuntimeError(f"секрет не найден в окружении: {name}")
        return os.environ[name]
    if isinstance(value, list):
        return [_resolve_secrets(v) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_secrets(v) for k, v in value.items()}
    return value


def _eval(expr, env, registry, hook, trace, state: _State):
    kind = expr[0]
    if kind == "lit":
        return expr[1]
    if kind == "var":
        if expr[1] not in env:
            raise PickRuntimeError(f"переменная не определена: {expr[1]}")
        return env[expr[1]]
    if kind == "get":
        parts = expr[1].split(".")
        if parts[0] not in env:
            raise PickRuntimeError(f"переменная не определена: {parts[0]}")
        value = env[parts[0]]
        for part in parts[1:]:
            value = value[part] if isinstance(value, dict) else getattr(value, part)
        return value
    if kind == "map":
        return {k: _eval(v, env, registry, hook, trace, state) for k, v in expr[1]}
    if kind == "cmp":
        _, op, left, right = expr
        a, b = _eval(left, env, registry, hook, trace, state), _eval(right, env, registry, hook, trace, state)
        return {"==": a == b, "!=": a != b, ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
    if kind == "pipe":
        value = _eval(expr[1][0], env, registry, hook, trace, state)
        for stage in expr[1][1:]:
            if stage[0] != "call":
                raise PickRuntimeError("в пайплайне после >> ожидается вызов")
            value = _call(stage, env, registry, hook, trace, state, piped=value)
        return value
    if kind == "call":
        return _call(expr, env, registry, hook, trace, state)
    raise PickRuntimeError(f"unknown expr {kind}")


def _call(expr, env, registry, hook, trace, state: _State, piped=...):
    _, name, arg_exprs = expr
    if state.capabilities is not None and name not in state.capabilities:
        raise PickRuntimeError(f"capability не выдана программе: {name}")
    args = [_eval(a, env, registry, hook, trace, state) for a in arg_exprs]
    if piped is not ...:
        args = [piped] + args
    if state.resolve_secrets:
        args = [_resolve_secrets(a) for a in args]
    fn = registry.get(name)
    if fn is None:
        raise PickRuntimeError(f"функция не зарегистрирована хостом: {name}")
    # Taint: недоверенные данные не уходят в опасные приёмники без одобрения.
    def _tainted_expr(e):
        if not isinstance(e, tuple):
            return False
        if e[0] in ("var", "get"):
            return e[1].split(".")[0] in state.tainted
        return any(_tainted_expr(x) for x in e[1:] if isinstance(x, (tuple, list))) or \
            any(_tainted_expr(x) for part in e[1:] if isinstance(part, list) for x in part if isinstance(x, tuple))
    if name in ("mail.send", "bank.charge", "bank.refund", "db.write") and not state.approved and \
            any(_tainted_expr(a) for a in arg_exprs):
        raise PickRuntimeError(f"taint: результат недоверенного инструмента идёт в {name} без @wait approval")
    trace.append(name)
    result = hook(name, fn, args) if hook is not None else fn(*args)
    return state.charge_call(name, result)


def _aot_eligible(source: str, activity_hook, capabilities, untrusted) -> bool:
    """AOT — быстрый путь без саг, бюджетов, taint, capabilities и durable-хуков."""
    if activity_hook is not None or capabilities is not None or untrusted:
        return False
    if "secret:" in source or "!budget" in source:
        return False
    try:
        steps = _parse_cached(source)
    except Exception:
        return False
    if any(s.kind in ("wait", "parallel") for s in steps):
        # AOT currently executes parallel branches sequentially; keep interpreter semantics.
        return False
    return all((s.retries or 0) == 0 and s.effect is None for s in steps)


def run_program(source: str, registry: Registry, inputs: dict[str, Any] | None = None,
                signal_provider: Callable[[str], Any] | None = None,
                activity_hook: Callable[[str, Callable, list], Any] | None = None,
                capabilities: set[str] | None = None,
                untrusted: tuple[str, ...] | set[str] = (),
                prefer_aot: bool = True):
    """Возвращает (output, env, trace). Бросает WaitingSignal, если сигнала ещё нет."""
    if prefer_aot and _aot_eligible(source, activity_hook, capabilities, untrusted):
        from .aot import DoneWhenFailed, run_compiled
        trace: list[str] = []
        def _traced(name, fn):
            def call(*args):
                trace.append(name)
                return fn(*args)
            return call
        traced_registry = {name: _traced(name, fn) for name, fn in registry.items()}
        env0 = dict(inputs or {})
        try:
            out, env_out = run_compiled(source, traced_registry, env0, signal_provider)
        except DoneWhenFailed as e:
            raise TaskNotDone(str(e)) from e
        except RuntimeError as e:
            raise PickRuntimeError(str(e)) from e
        return out, env_out, trace
    steps = _parse_cached(source)
    env: dict[str, Any] = dict(inputs or {})
    compensations: list = []
    trace: list[str] = []
    state = _State(capabilities=capabilities, untrusted=untrusted)
    state.exact_tokens = any(s.kind == "budget" and s.budget and "tokens" in s.budget for s in steps)
    state.approved = bool(env.get("approval"))
    output = None
    done_conditions: list = []
    def execute_assign(step):
        attempts = (step.retries or 0) + 1
        last = None
        for _attempt in range(attempts):
            try:
                value = _eval(step.expr, env, registry, activity_hook, trace, state)
                break
            except (WaitingSignal, AbortExecution, BudgetExceeded):
                raise
            except Exception as exc:
                last = exc
        else:
            raise last  # type: ignore[misc]
        if step.target:
            env[step.target] = value
            def _calls(e):
                if isinstance(e, tuple):
                    if e[0] == "call":
                        yield e[1]
                    for x in e[1:]:
                        if isinstance(x, tuple):
                            yield from _calls(x)
                        elif isinstance(x, list):
                            for y in x:
                                if isinstance(y, tuple):
                                    yield from _calls(y)
            if any(c in state.untrusted for c in _calls(step.expr)):
                state.tainted.add(step.target)
        if step.effect:
            state.effects.append(f"{step.effect}:{step.target or 'step'}")
        if step.compensation is not None:
            compensations.append(step.compensation)
        return value

    try:
        for step in steps:
            if step.kind == "goal":
                missing = [p for p in step.params if p not in env]
                if missing:
                    raise PickRuntimeError(f"не переданы параметры цели: {', '.join(missing)}")
            elif step.kind == "budget":
                state.budget.update(step.budget)
            elif step.kind == "donewhen":
                done_conditions = step.expr
            elif step.kind == "parallel":
                def run_branch(branch):
                    target, expr = branch
                    return target, _eval(expr, dict(env), registry, activity_hook, trace, state)
                with ThreadPoolExecutor(max_workers=max(2, len(step.branches))) as pool:
                    for target, value in pool.map(run_branch, step.branches):
                        if target:
                            env[target] = value
            elif step.kind == "loop":
                items = _eval(step.expr, env, registry, activity_hook, trace, state)
                body = step.body
                # П.6: если у функции тела есть batch-вариант (fn.batch в реестре),
                # вся коллекция уходит одним вызовом вместо roundtrip на элемент.
                batch_fn = None
                if body.kind == "assign" and body.expr[0] == "call":
                    cand = registry.get(body.expr[1] + ".batch")
                    if cand is not None:
                        batch_fn = (body.expr[1] + ".batch", cand)
                if batch_fn is not None:
                    name, fn = batch_fn
                    trace.append(name)
                    args = [_eval(a, env, registry, activity_hook, trace, state) for a in body.expr[2][1:]]
                    results = fn(list(items), *args)
                    if body.target:
                        env[body.target] = results
                    env[step.loop_var] = items[-1] if items else None
                else:
                    for item in items:
                        env[step.loop_var] = item
                        if body.kind in ("assign", "expr"):
                            execute_assign(body)
                        else:
                            raise PickRuntimeError("в теле for поддержан только шаг-вызов или присваивание")
            elif step.kind in ("assign", "expr"):
                execute_assign(step)
            elif step.kind == "guard":
                ok = _eval(step.expr, env, registry, activity_hook, trace, state)
                if not ok:
                    reason = _eval(step.target, env, registry, activity_hook, trace, state)
                    raise PickRuntimeError(f"guard failed: {reason}")
            elif step.kind == "wait":
                value = signal_provider(step.signal) if signal_provider else None
                if value is None:
                    if step.timeout is not None:
                        # синхронный провайдер: нет сигнала = таймаут уже наступил
                        if step.else_step is not None:
                            alt = step.else_step
                            if alt.kind in ("assign", "expr"):
                                execute_assign(alt)
                                continue
                        raise PickRuntimeError(f"таймаут ожидания сигнала {step.signal} ({step.timeout}с)")
                    raise WaitingSignal(step.signal)
                env[step.signal] = value
                if step.signal == "approval":
                    state.approved = bool(value)
            elif step.kind == "out":
                output = _eval(step.expr, env, registry, activity_hook, trace, state)
    except (PickRuntimeError, WaitingSignal, AbortExecution):
        raise
    except Exception as exc:
        comp_trace: list[str] = []
        for comp in reversed(compensations):
            try:
                _eval(comp, env, registry, activity_hook, comp_trace, state)
            except Exception:
                pass
        raise PickRuntimeError(f"выполнение сорвалось, компенсаций выполнено: {len(comp_trace)}; причина: {exc}") from exc
    for cond in done_conditions:
        if not _eval(cond, env, registry, activity_hook, trace, state):
            raise TaskNotDone("критерии готовности (done when) не выполнены")
    return output, env, trace
