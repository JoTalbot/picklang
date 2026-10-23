"""Компиляция PICK в Canonical IR (JSON) + digest программы.

IR — то, что реально исполняется и хранится в CAS. LLM в следующей итерации
ссылается на digest программы, а не пересылает её целиком (см. diff-режим).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .parser import Step, parse


def _expr_ir(expr) -> Any:
    kind = expr[0]
    if kind == "bool":
        return {"bool": [expr[1], [_expr_ir(e) for e in expr[2]]]}
    if kind == "call":
        return {"call": expr[1], "args": [_expr_ir(a) for a in expr[2]]}
    if kind == "var":
        return {"var": expr[1]}
    if kind == "lit":
        return {"lit": expr[1]}
    if kind == "get":
        return {"get": expr[1]}
    if kind == "map":
        return {"map": [[k, _expr_ir(v)] for k, v in expr[1]]}
    if kind == "cmp":
        return {"cmp": [expr[1], _expr_ir(expr[2]), _expr_ir(expr[3])]}
    if kind == "pipe":
        return {"pipe": [_expr_ir(e) for e in expr[1]]}
    raise ValueError(kind)


def compile_ir(source: str) -> dict[str, Any]:
    return {"lang": "pick", "version": "0.1", "steps": _steps_ir(parse(source))}


def _steps_ir(steps):
    ir_steps = []
    for s in steps:
        item: dict[str, Any] = {"kind": s.kind, "line": s.line}
        if s.kind == "conditional":
            item["cases"] = [{"condition": _expr_ir(cond) if cond is not None else None,
                              "steps": _steps_ir(body)} for cond, body in s.cases]
        if s.name: item["name"] = s.name
        if s.params: item["params"] = s.params
        if s.target and s.kind == "assign": item["target"] = s.target
        if s.retries: item["retries"] = s.retries
        if s.effect: item["effect"] = s.effect
        if s.signal: item["signal"] = s.signal
        if s.timeout is not None: item["timeout"] = s.timeout
        if s.else_step is not None:
            alt = s.else_step
            else_item: dict[str, Any] = {"kind": alt.kind, "line": alt.line}
            if alt.target and alt.kind == "assign": else_item["target"] = alt.target
            if alt.retries: else_item["retries"] = alt.retries
            if alt.effect: else_item["effect"] = alt.effect
            if alt.expr is not None: else_item["expr"] = _expr_ir(alt.expr)
            if alt.compensation is not None:
                else_item["compensation"] = _expr_ir(alt.compensation)
            item["else_step"] = else_item
        if s.budget: item["budget"] = s.budget
        if s.branches: item["branches"] = [[tgt, _expr_ir(e)] for tgt, e in s.branches]
        if s.kind == "loop":
            item["loop_var"] = s.loop_var
            b = s.body
            body_item = {"kind": b.kind, "target": b.target,
                         "expr": _expr_ir(b.expr) if b.expr is not None else None}
            if b.retries: body_item["retries"] = b.retries
            if b.effect: body_item["effect"] = b.effect
            if b.compensation is not None:
                body_item["compensation"] = _expr_ir(b.compensation)
            item["body"] = body_item
        if s.kind == "donewhen":
            item["conditions"] = [_expr_ir(e) for e in s.expr]
        elif s.expr is not None: item["expr"] = _expr_ir(s.expr)
        if s.kind == "guard": item["fail"] = _expr_ir(s.target)
        if s.compensation is not None: item["compensation"] = _expr_ir(s.compensation)
        ir_steps.append(item)
    return ir_steps


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def program_digest(source_or_ir: str | dict) -> str:
    ir = compile_ir(source_or_ir) if isinstance(source_or_ir, str) else source_or_ir
    return hashlib.sha256(canonical(ir)).hexdigest()
