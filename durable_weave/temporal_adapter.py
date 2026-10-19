"""П.7a — Temporal-адаптер того же Canonical IR.

PICK не меняется: та же программа (тот же IR, тот же digest) исполняется
настоящим Temporal вместо локального DurableExecutor. Соответствие:
  строка PICK (вызов)  -> Activity "pick.call" (хост-функция на воркере)
  @wait signal         -> workflow.wait_condition + @workflow.signal
  ? guard -> fail      -> ApplicationError (non-retryable)
  компенсации !        -> выполняются workflow-кодом в обратном порядке
Локальный executor остаётся для тестов и офлайна; Temporal — для прода.
"""
from __future__ import annotations

from typing import Any


def generate_temporal_code(workflow_name: str = "PickWorkflow") -> str:
    """Сгенерировать исходник Temporal-воркфлоу, исполняющего IR PICK."""
    return f'''# Сгенерировано temporal_adapter: исполнение Canonical IR (PICK) на Temporal.
import asyncio
from datetime import timedelta
from temporalio import workflow, activity
from temporalio.exceptions import ApplicationError


@activity.defn(name="pick.call")
async def pick_call(payload: dict) -> object:
    # На воркере сюда подставляется реестр хост-функций проекта.
    return HOST_REGISTRY[payload["name"]](*payload.get("args", []))


@workflow.defn(name="{workflow_name}")
class {workflow_name}:
    def __init__(self):
        self._signals = {{}}

    @workflow.signal
    async def pick_signal(self, payload: dict):
        self._signals[payload["name"]] = payload.get("value")

    @workflow.run
    async def run(self, ir: dict, inputs: dict):
        env = dict(inputs)
        for step in ir["steps"]:
            kind = step["kind"]
            if kind in ("assign", "expr"):
                result = await self._eval(step["expr"], env)
                if step.get("target"):
                    env[step["target"]] = result
            elif kind == "guard":
                if not await self._eval(step["expr"], env):
                    raise ApplicationError("guard failed", non_retryable=True)
            elif kind == "wait":
                name = step["signal"]
                await workflow.wait_condition(lambda: name in self._signals)
                env[name] = self._signals[name]
            elif kind == "out":
                return await self._eval(step["expr"], env)

    async def _eval(self, expr, env):
        # Чистая (детерминированная) часть: литералы/переменные/поля.
        # Вызовы уходят в Activity — как требует модель Temporal.
        if "lit" in expr: return expr["lit"]
        if "var" in expr: return env[expr["var"]]
        if "get" in expr:
            parts = expr["get"].split(".")
            value = env[parts[0]]
            for part in parts[1:]:
                value = value[part]
            return value
        if "map" in expr:
            return {{k: await self._eval(v, env) for k, v in expr["map"]}}
        if "pipe" in expr:
            value = await self._eval(expr["pipe"][0], env)
            for stage in expr["pipe"][1:]:
                args = [value] + [await self._eval(a, env) for a in stage["args"]]
                value = await workflow.execute_activity(
                    "pick.call", {{"name": stage["call"], "args": args}},
                    start_to_close_timeout=timedelta(minutes=5))
            return value
        if "call" in expr:
            args = [await self._eval(a, env) for a in expr["args"]]
            return await workflow.execute_activity(
                "pick.call", {{"name": expr["call"], "args": args}},
                start_to_close_timeout=timedelta(minutes=5))
        raise ApplicationError(f"unknown expr: {{expr}}", non_retryable=True)
'''


def build_temporal_workflow():
    """Вернуть настоящий класс workflow, если temporalio установлен."""
    return build_temporal_workflow_v2()


# --- Настоящие классы Temporal (если SDK установлен) --------------------
HOST_REGISTRY: dict[str, Any] = {}
try:  # pragma: no cover - зависит от окружения
    import asyncio

    from datetime import timedelta as _td

    from temporalio import activity as _activity
    from temporalio import workflow as _workflow
    from temporalio.exceptions import ApplicationError as _AppErr

    @_activity.defn(name="pick.call")
    async def pick_call(payload: dict):
        return HOST_REGISTRY[payload["name"]](*payload.get("args", []))

    @_workflow.defn(name="PickWorkflow")
    class PickWorkflow:
        def __init__(self):
            self._signals: dict[str, Any] = {}

        @_workflow.signal
        async def pick_signal(self, payload: dict):
            self._signals[payload["name"]] = payload.get("value")

        @_workflow.run
        async def run(self, ir: dict, inputs: dict):
            env = dict(inputs)
            compensations = []
            try:
                for step in ir["steps"]:
                    kind = step["kind"]
                    if kind in ("assign", "expr"):
                        result = await self._eval(step["expr"], env)
                        if step.get("target"):
                            env[step["target"]] = result
                        if "compensation" in step:
                            compensations.append(step["compensation"])
                    elif kind == "guard":
                        if not await self._eval(step["expr"], env):
                            raise _AppErr("guard failed", non_retryable=True)
                    elif kind == "wait":
                        name = step["signal"]
                        await _workflow.wait_condition(lambda: name in self._signals)
                        env[name] = self._signals[name]
                    elif kind == "loop":
                        items = await self._eval(step["expr"], env)
                        body = step["body"]
                        for item in items:
                            env[step["loop_var"]] = item
                            env[body["target"]] = await self._eval(body["expr"], env)
                    elif kind == "parallel":
                        values = await asyncio.gather(
                            *(self._eval(e, env) for _t, e in step["branches"]))
                        for (target, _e), value in zip(step["branches"], values):
                            if target:
                                env[target] = value
                    elif kind == "donewhen":
                        for cond in step["conditions"]:
                            if not await self._eval(cond, env):
                                raise _AppErr("done when не выполнен", non_retryable=True)
                    elif kind == "out":
                        return await self._eval(step["expr"], env)
            except Exception:
                # сага: компенсации activities в обратном порядке
                for comp in reversed(compensations):
                    try:
                        await self._eval(comp, env)
                    except Exception:
                        pass
                raise

        async def _eval(self, expr, env):
            if "lit" in expr:
                return expr["lit"]
            if "var" in expr:
                return env[expr["var"]]
            if "get" in expr:
                parts = expr["get"].split(".")
                value = env[parts[0]]
                for part in parts[1:]:
                    value = value[part]
                return value
            if "map" in expr:
                return {k: await self._eval(v, env) for k, v in expr["map"]}
            if "cmp" in expr:
                op, left, right = expr["cmp"]
                lv, rv = await self._eval(left, env), await self._eval(right, env)
                return {"==": lv == rv, "!=": lv != rv, ">": lv > rv, "<": lv < rv,
                        ">=": lv >= rv, "<=": lv <= rv}[op]
            if "pipe" in expr:
                value = await self._eval(expr["pipe"][0], env)
                for stage in expr["pipe"][1:]:
                    args = [value] + [await self._eval(a, env) for a in stage["args"]]
                    value = await _workflow.execute_activity(
                        "pick.call", {"name": stage["call"], "args": args},
                        start_to_close_timeout=_td(minutes=5))
                return value
            if "call" in expr:
                args = [await self._eval(a, env) for a in expr["args"]]
                return await _workflow.execute_activity(
                    "pick.call", {"name": expr["call"], "args": args},
                    start_to_close_timeout=_td(minutes=5))
            raise _AppErr(f"unknown expr: {expr}", non_retryable=True)

    TEMPORAL_AVAILABLE = True
except ImportError:  # pragma: no cover
    TEMPORAL_AVAILABLE = False
    PickWorkflow = None  # type: ignore
    pick_call = None  # type: ignore


def build_temporal_workflow_v2():
    if not TEMPORAL_AVAILABLE:
        raise RuntimeError("Для Temporal-адаптера нужен пакет temporalio: pip install temporalio")
    return PickWorkflow, pick_call
