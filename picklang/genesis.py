"""П.4 — Генератор программ из намерения (направление Genesis).

LLM платит только за то, что уникально в задаче: цель, шаги, компенсации,
стражей и ожидания перечисляет компактным намерением (dict/JSON), а типовую
механику в текст PICK достраивает генератор. Сгенерированная программа
обязательно проходит parser + статический контроль до возврата.
"""
from __future__ import annotations

from typing import Any

from .check import check_source


class GenesisError(Exception):
    pass


def _fmt_arg(a: Any) -> str:
    if isinstance(a, str) and ("." in a or a.isidentifier()):
        return a  # ссылка на переменную/поле, а не строка
    if isinstance(a, str):
        return f'"{a}"'
    if isinstance(a, bool):
        return "True" if a else "False"
    if isinstance(a, (int, float)):
        return str(a)
    if isinstance(a, dict) and "lit" in a:
        return repr(a["lit"])
    return str(a)


def _call(name: str, args: list | None) -> str:
    return f"{name}({', '.join(_fmt_arg(a) for a in (args or []))})"


def generate(intent: dict[str, Any], registry: dict | None = None) -> str:
    """Намерение -> PICK.

    intent = {
      "goal": "order", "params": ["id", "amt"],
      "steps": [
        {"do": "stock.reserve", "args": ["id"], "as": "r", "undo": "stock.release(id)"},
        {"do": "bank.charge", "args": ["id", "amt"], "as": "p", "undo_call": "bank.refund", "undo_args": ["p.tx"]},
        {"guard": "p.ok", "fail": "банк отклонил"},
        {"wait": "approval"},
        {"pipe": ["web.search(plan.query)", "docs.top(3)"] , "as": "found"},
      ],
      "out": {"order": "id", "tx": "p.tx"}   # или строкой "p.tx"
    }
    """
    if "goal" not in intent:
        raise GenesisError("в намерении нет goal")
    lines = [f"> {intent['goal']}({', '.join(intent.get('params', []))})"]
    for step in intent.get("steps", []):
        if "wait" in step:
            lines.append(f"@wait {step['wait']}")
            continue
        if "guard" in step:
            lines.append(f"? {step['guard']} -> fail \"{step.get('fail', 'условие не выполнено')}\"")
            continue
        if "use" in step:
            lines.append(f"&{step['use']}({', '.join(_fmt_arg(a) for a in step.get('args', []))})")
            continue
        if "pipe" in step:
            expr = " >> ".join(step["pipe"])
        elif "do" in step:
            expr = _call(step["do"], step.get("args"))
        else:
            raise GenesisError(f"не понимаю шаг намерения: {step}")
        undo = None
        if "undo" in step:
            undo = step["undo"]
        elif "undo_call" in step:
            undo = _call(step["undo_call"], step.get("undo_args"))
        target = step.get("as")
        line = f"{target} = {expr}" if target else expr
        if undo:
            line += f" ! {undo}"
        lines.append(line)
    out = intent.get("out")
    if isinstance(out, dict):
        body = ", ".join(f"{k}: {_fmt_arg(v)}" for k, v in out.items())
        lines.append(f"out {{{body}}}")
    elif isinstance(out, str):
        lines.append(f"out {out}")
    source = "\n".join(lines) + "\n"
    # Шаги {"use": ...} разворачивает словарь проекта позже: до развёртки
    # программу нельзя ни распарсить, ни проверить (переменные блоков не видны).
    has_vocab_steps = any("use" in s for s in intent.get("steps", []))
    diags = [] if has_vocab_steps else [d for d in check_source(source, registry) if d["code"] in ("UNDEFINED_VAR", "UNKNOWN_FN", "ARITY")]
    if diags:
        raise GenesisError(f"сгенерированная программа не прошла контроль: {diags}")
    return source


# --- Готовые шаблоны типовых форм --------------------------------------
def saga_template(goal: str, params: list[str], steps: list[dict], out: Any) -> str:
    return generate({"goal": goal, "params": params, "steps": steps, "out": out})


def agent_template(goal: str, param: str, plan_fn: str, risky_call: str, out: Any) -> str:
    return generate({
        "goal": goal, "params": [param],
        "steps": [
            {"do": "llm.plan", "args": [param], "as": "plan"},
            {"do": risky_call, "args": ["plan"], "as": "draft"},
            {"wait": "approval"},
            {"guard": "approval", "fail": "человек отклонил действие"},
        ],
        "out": out,
    })
