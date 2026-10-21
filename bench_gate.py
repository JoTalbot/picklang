#!/usr/bin/env python3
"""Release gate for the paired PICK-vs-classic benchmark."""
import json
import sys
from pathlib import Path


def evaluate_report(report):
    if report.get("status") == "INCONCLUSIVE_RATE_LIMIT" or report.get("rate_limit_detected"):
        return False, "INCONCLUSIVE: provider rate limit; refusing to publish without efficiency evidence"
    tasks = report.get("tasks")
    try:
        repeats = int(report.get("repeats", 1))
    except (TypeError, ValueError):
        return False, "invalid repeat count"
    if not isinstance(tasks, list) or not tasks or repeats < 1:
        return False, "invalid task list or repeat count"
    planned = len(tasks) * repeats
    pick = report.get("pick")
    baseline = report.get("baseline")
    if not isinstance(pick, list) or not isinstance(baseline, list):
        return False, "missing PICK or baseline rows"
    if len(pick) != planned or len(baseline) != planned:
        return False, f"incomplete report: PICK {len(pick)}/{planned}, Python {len(baseline)}/{planned}"
    pick_pass = sum(row.get("ok") is True for row in pick)
    baseline_pass = sum(row.get("ok") is True for row in baseline)
    pick_matches = sum(row.get("output_match") is True for row in pick)
    baseline_matches = sum(row.get("output_match") is True for row in baseline)
    if (pick_pass != planned or baseline_pass != planned
            or pick_matches != planned or baseline_matches != planned):
        return False, (
            f"quality gate failed: PICK {pick_pass}/{planned}, Python {baseline_pass}/{planned}, "
            f"matched outputs PICK {pick_matches}/{planned}, Python {baseline_matches}/{planned}"
        )

    def total_tokens(rows):
        return sum(
            (row.get("prompt_tokens", 0) or 0) + (row.get("completion_tokens", 0) or 0)
            for row in rows
        )

    pick_tokens, baseline_tokens = total_tokens(pick), total_tokens(baseline)
    if pick_tokens <= 0 or baseline_tokens <= 0:
        return False, "token telemetry missing or zero; cannot verify cost improvement"
    if pick_tokens >= baseline_tokens:
        return False, (
            f"efficiency gate failed: PICK {pick_tokens} tokens is not below "
            f"classic Python {baseline_tokens} tokens"
        )
    saved = baseline_tokens - pick_tokens
    pct = saved / baseline_tokens * 100
    return True, (
        f"quality and efficiency gates passed: {planned}/{planned} matched tasks; "
        f"PICK {pick_tokens} tokens vs Python {baseline_tokens} ({saved} saved, {pct:.1f}% less)"
    )


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python bench_gate.py REPORT.json", file=sys.stderr)
        return 2
    try:
        report = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read paired benchmark report: {exc}", file=sys.stderr)
        return 2
    passed, message = evaluate_report(report)
    print(message)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
