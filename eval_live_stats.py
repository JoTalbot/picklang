#!/usr/bin/env python3
"""Накопление статистики живых прогонов: 5 раундов x 3 задачи, sequential каскад.

Каждый раунд дописывается в bench/live_stats.jsonl сразу — прерванный сбор
не теряет готовые раунды. Запуск: python3 eval_live_stats.py --key-file /path/key
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_live_cascade import TASKS, make_validator, GRAMMAR   # noqa: E402
from picklang.cascade import ModelCascade                      # noqa: E402
from picklang.live_agent import load_key                        # noqa: E402

TIERS = [["liquid/lfm-2.5-2.6b:free", "poolside/laguna-xs-2.1:free"],
         ["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free"]]
NAMES = ["charge_tx", "double_last", "guard_charge"]
OUT = Path("bench/live_stats.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()
    key = load_key(args.key_file)
    tasks = [t for t in TASKS if t["name"] in NAMES]
    for rnd in range(1, args.rounds + 1):
        for task in tasks:
            prompt = GRAMMAR + f"\nФункции хоста: {', '.join(task['tools'])}\nВход: {list(task['inputs'])}\nЗадача: {task['task']}\n"
            casc = ModelCascade(TIERS, per_model_timeout=args.timeout)
            t0 = time.time()
            row = {"round": rnd, "task": task["name"], "ok": False, "tier": None, "model": None}
            try:
                res = casc.run(key, [{"role": "user", "content": prompt}], make_validator(task))
                row.update(ok=True, tier=res["tier"], model=res["model"])
            except Exception as e:
                row["error"] = str(e)[:120]
            row["seconds"] = round(time.time() - t0, 1)
            row["attempts"] = len(casc.log)
            row["log_errors"] = [e.get("error", "")[:100] for e in casc.log if e.get("error")]
            row["tokens"] = sum((e.get("usage") or {}).get("total_tokens", 0) or 0 for e in casc.log)
            with OUT.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"раунд {rnd} {task['name']}: ok={row['ok']} tier={row['tier']} {row['seconds']}с tok={row['tokens']}", flush=True)
    print("готово:", OUT)


if __name__ == "__main__":
    main()
