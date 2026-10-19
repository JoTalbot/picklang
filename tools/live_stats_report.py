#!/usr/bin/env python3
"""Сводная статистика живых прогонов из всех накопленных срезов.

Источники: bench/live_cascade_results.json, bench/live_hedge_results.json,
bench/live_stats.jsonl. Ошибки квоты (429 free-models-per-day) считаются
ОТДЕЛЬНО от провалов моделей — иначе статистика врёт.
Запуск: python3 tools/live_stats_report.py [--write]
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    rows = []
    p = Path("bench/live_cascade_results.json")
    if p.exists():
        for r in json.loads(p.read_text()):
            rows.append({"src": "cascade", "task": r["task"], "ok": r["ok"], "tier": r["tier"],
                         "seconds": r["seconds"], "tokens": r["prompt_tokens"] + r["completion_tokens"],
                         "quota": False})
    p = Path("bench/live_hedge_results.json")
    if p.exists():
        for r in json.loads(p.read_text()):
            if r["mode"] == "sequential":
                rows.append({"src": "hedge-seq", "task": r["task"], "ok": r["ok"], "tier": r["tier"],
                             "seconds": r["seconds"], "tokens": r["tokens"], "quota": False})
    p = Path("bench/live_stats.jsonl")
    quota = 0
    if p.exists():
        for line in p.read_text().splitlines():
            r = json.loads(line)
            # квотные отказы опознаются по подписи: все модели упали мгновенно,
            # 0 токенов, <2 с на 4 попытки (прямая проверка API подтвердила 429)
            if not r["ok"] and r.get("tokens", 0) == 0 and r.get("seconds", 99) < 2:
                quota += 1
            else:
                rows.append({"src": f"stats{r['round']}", "task": r["task"], "ok": r["ok"], "tier": r["tier"],
                             "seconds": r["seconds"], "tokens": r["tokens"], "quota": False})
    ok = [r for r in rows if r["ok"]]
    tier0 = [r for r in ok if r["tier"] == 0]
    lines = [f"Всего модельных срезов (без квотных отказов): {len(rows)}, решено: {len(ok)}, "
             f"из них tier-0: {len(tier0)} ({100 * len(tier0) / max(len(ok), 1):.0f}%)",
             f"Отказов по дневной квоте free tier (429, не провалы моделей): {quota}"]
    if ok:
        lines.append(f"Медиана времени решённой задачи: {statistics.median(r['seconds'] for r in ok):.1f} с; "
                     f"медиана токенов: {statistics.median(r['tokens'] for r in ok):.0f}")
    for ln in lines:
        print(ln)
    if "--write" in sys.argv:
        doc = ["# Сводная статистика живых прогонов", "",
               "Источники: живой каскад 6 задач, A/B sequential/hedged, накопление по раундам.", ""]
        doc += [f"- {ln}" for ln in lines]
        doc += ["", "Главное ограничение метода: дневная квота free tier — 50 запросов/день на ключ; "
                      "накопление 5 раундов уперлось в неё целиком (все 15 попыток раундов — 429). "
                      "Статистика выше поэтому собрана из срезов до исчерпания квоты; "
                      "для честных медиан по раундам нужен либо другой день, либо платный ключ."]
        Path("docs/LIVE_STATS.md").write_text("\n".join(doc) + "\n", encoding="utf-8")
        print("docs/LIVE_STATS.md обновлён")


if __name__ == "__main__":
    main()
