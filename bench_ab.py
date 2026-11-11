#!/usr/bin/env python3
"""Compare paired agent-evaluation JSON runs; no API calls or guessed prices."""
import argparse
import json
import math
from pathlib import Path


def _non_negative_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def load_rows(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}: expected non-empty JSON array")
    rows = {}
    for row in raw:
        if not isinstance(row, dict) or not isinstance(row.get("task"), str) or not row["task"]:
            raise ValueError(f"{path}: every row needs a task name")
        name = row["task"]
        if name in rows:
            raise ValueError(f"{path}: duplicate task {name!r}")
        if not isinstance(row.get("ok"), bool):
            raise ValueError(f"{path}: {name}.ok must be a boolean")
        model = row.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"{path}: {name}.model must be a non-empty string")
        for field in ("prompt_tokens", "completion_tokens", "attempts"):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{path}: {name}.{field} must be a non-negative integer")
        if not _non_negative_number(row.get("seconds")):
            raise ValueError(f"{path}: {name}.seconds must be a finite non-negative number")
        rows[name] = row
    return rows


def estimate_cost(row, prices):
    if prices is None:
        return None
    price = prices.get(row.get("model"))
    if not isinstance(price, dict):
        return None
    rates = (price.get("prompt_per_million"), price.get("completion_per_million"))
    if not all(_non_negative_number(value) for value in rates):
        raise ValueError(f"invalid pricing for model {row.get('model')!r}")
    return (
        row["prompt_tokens"] * rates[0] + row["completion_tokens"] * rates[1]
    ) / 1_000_000


def summarize(rows, prices=None):
    n = len(rows)
    passed = sum(row["ok"] is True for row in rows.values())
    costs = [estimate_cost(row, prices) for row in rows.values()]
    prompt = sum(row["prompt_tokens"] for row in rows.values())
    completion = sum(row["completion_tokens"] for row in rows.values())
    return {
        "tasks": n,
        "passed": passed,
        "pass_rate": passed / n,
        "attempts": sum(row["attempts"] for row in rows.values()),
        "wall_seconds_sum": sum(row["seconds"] for row in rows.values()),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "estimated_cost_usd": sum(costs) if all(cost is not None for cost in costs) else None,
    }


def compare(pick, baseline, pick_prices=None, baseline_prices=None):
    if set(pick) != set(baseline):
        raise ValueError(
            f"task sets differ; missing from PICK={sorted(set(baseline)-set(pick))}, "
            f"missing from baseline={sorted(set(pick)-set(baseline))}"
        )
    return {
        "tasks": [
            {
                "task": name,
                "pick_ok": pick[name]["ok"],
                "baseline_ok": baseline[name]["ok"],
                "pick_model": pick[name]["model"],
                "baseline_model": baseline[name]["model"],
            }
            for name in pick
        ],
        "pick": summarize(pick, pick_prices),
        "baseline": summarize(baseline, baseline_prices),
        "warning": (
            "Cost is estimated only when prices are supplied for every model. "
            "Token usage is not semantic equivalence; compare outputs and repeat runs "
            "before claiming general superiority."
        ),
    }


def markdown(report):
    pick, baseline = report["pick"], report["baseline"]
    lines = [
        "# Paired live A/B evaluation",
        "",
        "| Metric | PICK | Classic baseline |",
        "|---|---:|---:|",
        f"| Tasks passed | {pick['passed']}/{pick['tasks']} | {baseline['passed']}/{baseline['tasks']} |",
        f"| Pass rate | {pick['pass_rate']:.1%} | {baseline['pass_rate']:.1%} |",
        f"| Attempts | {pick['attempts']} | {baseline['attempts']} |",
        f"| Sum of per-task durations (s) | {pick['wall_seconds_sum']:.2f} | {baseline['wall_seconds_sum']:.2f} |",
        f"| Prompt tokens | {pick['prompt_tokens']} | {baseline['prompt_tokens']} |",
        f"| Completion tokens | {pick['completion_tokens']} | {baseline['completion_tokens']} |",
        f"| Total tokens | {pick['total_tokens']} | {baseline['total_tokens']} |",
    ]
    if pick["estimated_cost_usd"] is not None and baseline["estimated_cost_usd"] is not None:
        lines.append(
            f"| Estimated API cost (USD) | {pick['estimated_cost_usd']:.6f} "
            f"| {baseline['estimated_cost_usd']:.6f} |"
        )
    lines += [
        "",
        "## Per-task parity",
        "",
        "| Task | PICK | Baseline | PICK model | Baseline model |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {row['task']} | {'PASS' if row['pick_ok'] else 'FAIL'} "
        f"| {'PASS' if row['baseline_ok'] else 'FAIL'} "
        f"| {row['pick_model']} | {row['baseline_model']} |"
        for row in report["tasks"]
    ]
    lines += ["", "> Limitation: " + report["warning"], ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pick", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--pick-prices")
    ap.add_argument("--baseline-prices")
    ap.add_argument("--write")
    args = ap.parse_args()

    def load_prices(path):
        if not path:
            return None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a JSON object keyed by model")
        return data

    output = markdown(
        compare(
            load_rows(args.pick),
            load_rows(args.baseline),
            load_prices(args.pick_prices),
            load_prices(args.baseline_prices),
        )
    )
    print(output)
    if args.write:
        Path(args.write).write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
