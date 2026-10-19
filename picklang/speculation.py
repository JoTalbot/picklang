"""П.8 — Спекулятивные ветки в песочнице: record/replay инструментов.

Реальные ответы инструментов один раз записываются в фикстуры; дальше
планы и ветки гоняются по записи без живых сайд-эффектов. Ветка, сорвавшаяся
на моках, не идёт в прод. Неизвестный в записи вызов — ошибка, а не сеть.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from durable_weave.cas import canonical_json, sha256_hex


def _key(name: str, args: list) -> str:
    return sha256_hex(canonical_json({"fn": name, "args": args}))


class FixtureStore:
    def __init__(self, path: str | Path | None = None):
        self.fixtures: dict[str, Any] = {}
        self.path = Path(path) if path else None
        if self.path and self.path.exists():
            self.fixtures = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self):
        if self.path:
            self.path.write_text(json.dumps(self.fixtures, ensure_ascii=False), encoding="utf-8")

    def recording_registry(self, registry: dict[str, Callable]) -> dict[str, Callable]:
        out = {}
        for name, fn in registry.items():
            def make(name=name, fn=fn):
                def call(*args):
                    result = fn(*args)
                    self.fixtures[_key(name, list(args))] = result
                    self.save()
                    return result
                return call
            out[name] = make()
        return out

    def replay_registry(self, registry_names) -> dict[str, Callable]:
        out = {}
        for name in registry_names:
            def make(name=name):
                def call(*args):
                    key = _key(name, list(args))
                    if key not in self.fixtures:
                        raise KeyError(f"в записи нет вызова {name}{args}: песочница не ходит в живую")
                    return self.fixtures[key]
                return call
            out[name] = make()
        return out


def evaluate_branch(source: str, replay_registry: dict, inputs: dict) -> dict[str, Any]:
    """Прогнать ветку в песочнице и оценить: дошла / где сорвалась."""
    from .interpreter import run_program
    try:
        out, _env, trace = run_program(source, replay_registry, inputs,
                                       signal_provider=lambda s: True)
        return {"ok": True, "output": out, "calls": len(trace)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "calls": 0}
