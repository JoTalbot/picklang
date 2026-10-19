"""П.6 — Схема намерения для constrained decoding (structured outputs).

Отдаётся провайдеру как response_format json_schema: модель физически не
сможет вернуть намерение вне грамматики — половина цикла исправлений
исчезает до того, как началась.
"""
from __future__ import annotations


def intent_schema() -> dict:
    step = {
        "type": "object",
        "properties": {
            "do": {"type": "string"}, "args": {"type": "array"},
            "as": {"type": "string"}, "undo": {"type": "string"},
            "undo_call": {"type": "string"}, "undo_args": {"type": "array"},
            "use": {"type": "string"}, "guard": {"type": "string"},
            "fail": {"type": "string"}, "wait": {"type": "string"},
            "pipe": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    return {
        "name": "pick_intent",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "params": {"type": "array", "items": {"type": "string"}},
                "budget": {"type": "object"},
                "steps": {"type": "array", "items": step},
                "done_when": {"type": "array", "items": {"type": "string"}},
                "out": {},
            },
            "required": ["goal", "steps"],
            "additionalProperties": False,
        },
    }


def intent_gbnf() -> str:
    """Тот же контракт в виде GBNF-грамматики для локальных движков (llama.cpp)."""
    return r'''root ::= "{" ws "\"goal\"" ws ":" ws string "," ws "\"steps\"" ws ":" ws steps ws "}"
steps ::= "[" ws (step ("," ws step)*)? ws "]"
step ::= "{" ws (do-step | use-step | guard-step | wait-step) ws "}"
string ::= "\"" [^"]* "\""
ws ::= [ ]*
'''
