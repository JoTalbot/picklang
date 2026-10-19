#!/usr/bin/env python3
"""Живой A/B: последовательный каскад против hedged на 3 задачах OpenRouter.

Порядок честный: сначала sequential на всех задачах, потом hedged на тех же.
Free tier шумит (429, зависания), поэтому цифры — один срез, а не статистика.
Запуск: python3 eval_live_hedge.py --key-file /path/key [--write]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_live_cascade import GRAMMAR, TASKS, make_validator   # noqa: E402
from picklang.cascade import HedgedCascade, ModelCascade       # noqa: E402
from picklang.live_agent import load_key                        # noqa: E402

NAMES = ["charge_tx", "double_last", "guard_charge"]
TIERS = [["liquid/lfm-2.5-2.6b:free", "poolside/laguna-xs-2.1:free"],
         ["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free"]]


def prompt(task):
    return GRAMMAR + f"\nФункции хоста: {', '.join(task['tools'])}\nВход: {list(task['inputs'])}\nЗадача: {task['task']}\n"


def run_one(mode, key, task, timeout):
    msgs = [{"role": "user", "content": prompt(task)}]
    casc = (HedgedCascade(TIERS, per_model_timeout=timeout) if mode == "hedged"
            else ModelCascade(TIERS, per_model_timeout=timeout))
    t0 = time.time()
    row = {"mode": mode, "task": task["name"], "ok": False, "model": None, "tier": None, "attempts": 0, "tokens": 0}
    try:
        res = (casc.run_hedged(key, msgs, make_validator(task), hedge_delay=8.0) if mode == "hedged"
               else casc.run(key, msgs, make_validator(task)))
        row.update(ok=True, model=res["model"], tier=res["tier"])
    except Exception as e:
        row["error"] = str(e)[:150]
    row["seconds"] = round(time.time() - t0, 1)
    row["attempts"] = len(casc.log)
    row["tokens"] = sum((e.get("usage") or {}).get("total_tokens", 0) or 0 for e in casc.log)
    print(f"[{mode}] {task['name']}: ok={row['ok']} {row['seconds']}с попыток={row['attempts']} tok={row['tokens']} model={row['model']}", flush=True)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--timeout", type=int, default=40)
    args = ap.parse_args()
    key = load_key(args.key_file)
    tasks = [t for t in TASKS if t["name"] in NAMES]
    rows = []
    for mode in ("sequential", "hedged"):
        for task in tasks:
            rows.append(run_one(mode, key, task, args.timeout))
    for mode in ("sequential", "hedged"):
        sel = [r for r in rows if r["mode"] == mode]
        print(f"{mode}: {sum(r['ok'] for r in sel)}/{len(sel)} задач, "
              f"{sum(r['seconds'] for r in sel):.0f} с суммарно, {sum(r['tokens'] for r in sel)} токенов")
    Path("bench/live_hedge_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.write:
        lines = ["# Живой A/B: sequential против hedged каскада (OpenRouter, free, $0)", "",
                 "| Режим | Задача | OK | Сек | Попыток | Токенов | Модель |", "|---|---|---|---:|---:|---:|---|"]
        for r in rows:
            lines.append(f"| {r['mode']} | {r['task']} | {r['ok']} | {r['seconds']} | {r['attempts']} | {r['tokens']} | {r['model']} |")
        for mode in ("sequential", "hedged"):
            sel = [r for r in rows if r["mode"] == mode]
            lines.append(f"\nИтого {mode}: {sum(r['ok'] for r in sel)}/{len(sel)}, "
                         f"{sum(r['seconds'] for r in sel):.0f} с, {sum(r['tokens'] for r in sel)} токенов.")
        lines += ["", "Оговорка: один срез на шумном free tier; hedged платит лишними вызовами за меньшее ожидание."]
        Path("docs/LIVE_HEDGE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("docs/LIVE_HEDGE.md обновлён")


if __name__ == "__main__":
    main()
