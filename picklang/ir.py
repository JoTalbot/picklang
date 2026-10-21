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
    steps = parse(source)
    ir_steps = []
    for s in steps:
        item: dict[str, Any] = {"kind": s.kind, "line": s.line}
        if s.name: item["name"] = s.name
        if s.params: item["params"] = s.params
        if s.target and s.kind == "assign": item["target"] = s.target
        if s.retries: item["retries"] = s.retries
        if s.effect: item["effect"] = s.effect
        if s.signal: item["signal"] = s.signal
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
    return {"lang": "pick", "version": "0.1", "steps": ir_steps}


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def program_digest(source_or_ir: str | dict) -> str:
    ir = compile_ir(source_or_ir) if isinstance(source_or_ir, str) else source_or_ir
    return hashlib.sha256(canonical(ir)).hexdigest()
