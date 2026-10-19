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


def main():
    raw = estimate_tokens(RESULTS)
    projected = estimate_tokens(fields(top(RESULTS, 3), "title", "url"))
    guard = ToolResultGuard({"web.search": lambda q: RESULTS}, max_tokens=120)
    guarded = guard.registry["web.search"]("запрос")
    guarded_tokens = estimate_tokens(guarded)
    print(f"результат web.search целиком в контекст: {raw} ток.")
    print(f"после проекций top(3)+fields(title,url):  {projected} ток.  (в {raw/projected:.0f} раз меньше)")
    print(f"ToolResultGuard (лимит 120, остальное в CAS по ref): {guarded_tokens} ток., "
          f"сэкономлено {guard.stats['tokens_saved']} ток. на одном вызове")
    print("вывод: один урезанный ответ инструмента перекрывает экономию на всём синтаксисе программы")


if __name__ == "__main__":
    main()
