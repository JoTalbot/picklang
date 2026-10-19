"""П.1 — Автомайнинг словаря из трасс исполнений.

Рантайм смотрит журналы вызовов, находит повторяющиеся цепочки и сам
предлагает блоки словаря с посчитанной экономией: сколько раз цепочка
встретилась и сколько токенов она съедает у модели каждый раз.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def mine_patterns(traces: list[list[str]], min_len: int = 3, min_count: int = 3) -> list[dict[str, Any]]:
    """Частые подпоследовательности вызовов длины >= min_len."""
    counter: Counter = Counter()
    for trace in traces:
        seen_in_trace = set()
        for n in range(min_len, min(6, len(trace) + 1)):
            for i in range(len(trace) - n + 1):
                gram = tuple(trace[i:i + n])
                if gram not in seen_in_trace:
                    counter[gram] += 1
                    seen_in_trace.add(gram)
    proposals = []
    for gram, count in counter.most_common():
        if count < min_count:
            continue
        # не предлагать подцепи уже предложенных более длинных цепочек
        if any(set(gram) < set(p["pattern"]) and count <= p["count"] for p in proposals):
            continue
        proposals.append({
            "pattern": list(gram), "count": count,
            "alias": "auto_" + "_".join(g.split(".")[-1] for g in gram[:2]),
            # модель печатает цепочку каждый раз: ~6 токенов на вызов против 3 на &alias(...)
            "tokens_saved_est": count * (len(gram) * 6 - 3),
        })
    return sorted(proposals, key=lambda p: -p["tokens_saved_est"])
