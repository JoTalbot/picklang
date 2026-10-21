#!/usr/bin/env python3
"""Release gate for the paired PICK-vs-classic benchmark."""
import json
import sys
from pathlib import Path


def evaluate_report(report):
    if not isinstance(report, dict):
        return False, "invalid report: expected a JSON object"
    if report.get("status") == "INCONCLUSIVE_RATE_LIMIT" or report.get("rate_limit_detected"):
        return False, "INCONCLUSIVE: provider rate limit; refusing to publish without efficiency evidence"
    if report.get("status") != "COMPLETED":
        return False, f"invalid report status: expected COMPLETED, got {report.get('status')!r}"
    tasks = report.get("tasks")
    try:
        repeats = int(report.get("repeats", 1))
    except (TypeError, ValueError):
        return False, "invalid repeat count"
    if not isinstance(tasks, list) or not tasks or repeats < 1:
        return False, "invalid task list or repeat count"
    if any(not isinstance(task, str) or not task for task in tasks) or len(set(tasks)) != len(tasks):
        return False, "invalid or duplicate task names"
    planned = len(tasks) * repeats
    pick = report.get("pick")
    baseline = report.get("baseline")
    if not isinstance(pick, list) or not isinstance(baseline, list):
        return False, "missing PICK or baseline rows"
    if len(pick) != planned or len(baseline) != planned:
        return False, f"incomplete report: PICK {len(pick)}/{planned}, Python {len(baseline)}/{planned}"

    # A valid aggregate is not enough: verify that each comparison really is the
    # same task, repeat, and input, and that model telemetry does not contradict
    # the same-model benchmark contract.
    for index, (pick_row, baseline_row) in enumerate(zip(pick, baseline)):
        expected_task = tasks[index % len(tasks)]
        expected_repeat = index // len(tasks) + 1
        for adapter, row in (("PICK", pick_row), ("Python", baseline_row)):
            if not isinstance(row, dict):
                return False, f"invalid {adapter} row at index {index}"
            if row.get("task") != expected_task or row.get("repeat") != expected_repeat:
                return False, (
                    f"pairing integrity failed at index {index}: expected "
                    f"{expected_task!r} repeat {expected_repeat}, got "
                    f"{row.get('task')!r} repeat {row.get('repeat')!r} for {adapter}"
                )
        pick_digest = pick_row.get("input_digest")
        baseline_digest = baseline_row.get("input_digest")
        if not isinstance(pick_digest, str) or not pick_digest or pick_digest != baseline_digest:
            return False, f"input integrity failed for {expected_task!r} repeat {expected_repeat}"
        pick_model = pick_row.get("model")
        baseline_model = baseline_row.get("model")
        if pick_model and baseline_model and pick_model != baseline_model:
            return False, (
                f"model mismatch for {expected_task!r} repeat {expected_repeat}: "
                f"PICK={pick_model!r}, Python={baseline_model!r}"
            )

    # Token usage is a per-invocation contract: reject missing, negative,
    # boolean, or non-integer telemetry instead of silently treating it as zero.
    for adapter, rows in (("PICK", pick), ("Python", baseline)):
        for index, row in enumerate(rows):
            for field in ("prompt_tokens", "completion_tokens"):
                value = row.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return False, (
                        f"invalid token telemetry for {adapter} row {index}: "
                        f"{field} must be a non-negative integer"
                    )

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
