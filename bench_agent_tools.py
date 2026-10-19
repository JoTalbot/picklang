#!/usr/bin/env python3
"""Замер агентной волны: сколько токенов съедает результат инструмента.

Сценарий: web.search возвращает 20 результатов с полными текстами.
Вариант А (как обычно): весь результат падает в контекст модели.
Вариант Б (наш): проекции top/fields режут его рантаймом до контекста,
а ToolResultGuard страхует лимитом с ссылкой в CAS.
Запуск: python3 bench_agent_tools.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from picklang.result_guard import ToolResultGuard, estimate_tokens  # noqa: E402
from picklang.stdlib import fields, top  # noqa: E402

RESULTS = [{"title": f"Результат {i}", "url": f"https://example.com/{i}",
            "body": "Полный текст страницы. " * 60, "noise": list(range(50))} for i in range(20)]


def projection_metrics(results):
    """Measure this synthetic fixture only; never imply an end-to-end agent speedup."""
    raw = estimate_tokens(results)
    projected_value = fields(top(results, 3), "title", "url")
    projected = estimate_tokens(projected_value)
    return {
        "raw_tokens": raw,
        "projected_tokens": projected,
        "ratio": raw / projected,
        "saved_percent": (raw - projected) * 100 / raw if raw else 0.0,
    }


def main():
    metrics = projection_metrics(RESULTS)
    raw = metrics["raw_tokens"]
    projected = metrics["projected_tokens"]
    guard = ToolResultGuard({"web.search": lambda q: RESULTS}, max_tokens=120)
    guarded = guard.registry["web.search"]("запрос")
    guarded_tokens = estimate_tokens(guarded)
    print(f"результат web.search целиком в контекст: {raw} ток.")
    print(f"после проекций top(3)+fields(title,url):  {projected} ток.  (отношение {metrics['ratio']:.2f}x; сокращение {metrics['saved_percent']:.1f}%)")
    print(f"ToolResultGuard (лимит 120, остальное в CAS по ref): {guarded_tokens} ток., "
          f"сэкономлено {guard.stats['tokens_saved']} ток. на одном вызове")
    print("ограничение: синтетический набор, один вызов; это не замер стоимости, качества или скорости полного агентного цикла.")


if __name__ == "__main__":
    main()
