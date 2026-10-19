"""П.7 — Статистика инструментов для планировщика.

Каждый вызов пишется в журнал статистики: счётчик, ошибки, задержки.
Планировщику отдаётся одна компактная таблица — выбор пути становится
арифметикой по истории, а не интуицией модели.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any, Callable


class ToolStats:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.data: dict[str, dict[str, Any]] = {}
        if self.path and self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def wrap_registry(self, registry: dict[str, Callable]) -> dict[str, Callable]:
        wrapped = {}
        for name, fn in registry.items():
            def make(name=name, fn=fn):
                def call(*args):
                    t0 = time.perf_counter()
                    try:
                        result = fn(*args)
                        self._record(name, (time.perf_counter() - t0) * 1000, True)
                        return result
                    except Exception:
                        self._record(name, (time.perf_counter() - t0) * 1000, False)
                        raise
                return call
            wrapped[name] = make()
        return wrapped

    def _record(self, name, latency_ms, ok):
        d = self.data.setdefault(name, {"calls": 0, "fails": 0, "lat": []})
        d["calls"] += 1
        d["fails"] += 0 if ok else 1
        d["lat"].append(round(latency_ms, 2))
        if self.path:
            self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def summary(self) -> dict[str, Any]:
        out = {}
        for name, d in self.data.items():
            out[name] = {"calls": d["calls"], "fail_rate": round(d["fails"] / max(1, d["calls"]), 2),
                         "p50_ms": round(statistics.median(d["lat"]), 1) if d["lat"] else 0}
        return out

    def planner_table(self) -> str:
        return "\n".join(f"{n}: вызовов {s['calls']}, падений {s['fail_rate']:.0%}, p50 {s['p50_ms']}мс"
                         for n, s in sorted(self.summary().items()))
