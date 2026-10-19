#!/usr/bin/env python3
"""Замер третьей волны: передача контекста и семантический кэш.

А) Передача состояния между агентами: вся история JSON-ом против одной
   строки handoff (digest + diff) — бандл едет файлом мимо контекста LLM.
Б) Повторная одинаковая подзадача у второго агента: вызовы модели и
   инструментов против попаданий в семантический кэш.
Запуск: python3 bench_agent_handoff.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from durable_weave.context_transport import ContextTransport  # noqa: E402
from durable_weave.slots import SlotStore                      # noqa: E402
from picklang.result_guard import estimate_tokens              # noqa: E402
from picklang.semcache import SemanticCache                    # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as tmp:
        a = SlotStore(Path(tmp) / "A")
        history = {"task": {"q": "исследуй рынок"},
                   "research": [{"title": f"источник {i}", "text": "много текста " * 40} for i in range(8)],
                   "draft": {"text": "черновик " * 30}, "approval": True}
        for k, v in history.items():
            a.put(k, v)
        transport = ContextTransport(Path(tmp) / "inbox")
        handoff = transport.publish(a, names=["task", "draft", "approval"],
                                    base={"task": history["task"], "draft": {"text": "старый"}, "approval": False})
        full = estimate_tokens(history)
        slim = estimate_tokens(handoff)
        print(f"передать контекст целиком (JSON истории): {full} ток.")
        print(f"handoff-строка (digest + слоты + diff):   {slim} ток.  (в {full/slim:.0f} раз меньше)")
        print(f"  бандл при этом едет файлом мимо окна модели: {handoff['handoff'][:16]}…")

        calls = {"llm": 0, "search": 0}
        cache = SemanticCache(Path(tmp) / "cache", namespace="prompts-v1")
        reg = cache.wrap_registry({
            "llm.plan": lambda q: calls.__setitem__("llm", calls["llm"] + 1) or {"query": q},
            "web.search": lambda q: calls.__setitem__("search", calls["search"] + 1) or ["r1", "r2"],
        }, cacheable={"llm.plan", "web.search"})
        reg["llm.plan"]("исследуй рынок"); reg["web.search"]("исследуй рынок")
        first = dict(calls)
        reg["llm.plan"]("исследуй рынок"); reg["web.search"]("исследуй рынок")  # второй агент, та же подзадача
        print(f"первый агент: вызовов {first}; второй агент (та же подзадача): новых вызовов "
              f"{calls['llm'] - first['llm'] + calls['search'] - first['search']}, попаданий в кэш: {cache.stats['hits']}")


if __name__ == "__main__":
    main()
