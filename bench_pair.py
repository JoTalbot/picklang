#!/usr/bin/env python3
"""Run two JSON-over-stdin task adapters on the same fixture set and save paired results."""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path


def load_tasks(path):
    tasks = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task file must be a non-empty JSON array")
    seen = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("task"), str) or not task["task"]:
            raise ValueError("each task must be an object with a non-empty task name")
        if task["task"] in seen:
            raise ValueError(f"duplicate task name: {task['task']}")
        if "input" not in task:
            raise ValueError(f"task {task['task']!r} is missing input")
        seen.add(task["task"])
    return tasks


def run_one(command, task, timeout):
    request = {"task": task["task"], "input": task["input"]}
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            input=json.dumps(request, ensure_ascii=False) + "\n",
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        elapsed = round(time.perf_counter() - started, 6)
        if proc.returncode:
            return {"task": task["task"], "ok": False, "seconds": elapsed,
                    "error": f"adapter exited {proc.returncode}", "stderr": proc.stderr[-2000:]}
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return {"task": task["task"], "ok": False, "seconds": elapsed,
                    "error": f"adapter stdout is not one JSON object: {exc}",
                    "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            return {"task": task["task"], "ok": False, "seconds": elapsed,
                    "error": "adapter must emit a JSON object with boolean 'ok'",
                    "stdout": proc.stdout[-2000:]}
        row = {"task": task["task"], "ok": result["ok"], "seconds": elapsed}
        if "output" in result:
            row["output"] = result["output"]
        if "error" in result:
            row["error"] = str(result["error"])[:1000]
        for field in ("prompt_tokens", "completion_tokens", "attempts", "model"):
            if field in result:
                row[field] = result[field]
        return row
    except subprocess.TimeoutExpired:
        elapsed = round(time.perf_counter() - started, 6)
        return {"task": task["task"], "ok": False, "seconds": elapsed,
                "error": f"adapter timed out after {timeout}s"}


def is_provider_rate_limit(row):
    """Recognize provider throttling without treating ordinary task failures as quota events."""
    text = " ".join(str(row.get(key, "")) for key in ("error", "stderr", "stdout")).lower()
    # Match an actual status code, not the digits "429" inside provider timing
    # telemetry such as 0.05942919. Word/decimal boundaries matter here.
    has_status_429 = re.search(r"(?<![A-Za-z0-9_.])429(?![A-Za-z0-9_.])", text) is not None
    return (
        has_status_429
        or "rate limit" in text
        or "rate_limit" in text
        or "quota exceeded" in text
        or "too many requests" in text
    )


def skipped_row(task, repeat, reason):
    return {
        "task": task["task"],
        "repeat": repeat,
        "ok": False,
        "seconds": 0,
        "error": reason,
        "skipped": True,
    }


def median(values):
    """Return the median of numeric values, or None when no measurements exist."""
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return round((values[middle - 1] + values[middle]) / 2, 6)


def summarize_performance(rows, tasks):
    """Summarize repeated measurements without counting skipped/failed calls as latency wins."""
    successful = [row for row in rows if row.get("ok") and not row.get("skipped")]
    task_summaries = []
    for task in tasks:
        task_rows = [row for row in successful if row.get("task") == task["task"]]
        item = {
            "task": task["task"],
            "successful_runs": len(task_rows),
            "median_seconds": median([row["seconds"] for row in task_rows if isinstance(row.get("seconds"), (int, float))]),
        }
        for field in ("prompt_tokens", "completion_tokens", "attempts"):
            vals = [row[field] for row in task_rows if isinstance(row.get(field), (int, float))]
            item["median_" + field] = median(vals)
        item["total_reported_tokens"] = sum(
            row.get("prompt_tokens", 0) + row.get("completion_tokens", 0)
            for row in task_rows
            if isinstance(row.get("prompt_tokens", 0), (int, float))
            and isinstance(row.get("completion_tokens", 0), (int, float))
        )
        task_summaries.append(item)
    total_tokens = sum(
        row.get("prompt_tokens", 0) + row.get("completion_tokens", 0)
        for row in successful
        if isinstance(row.get("prompt_tokens", 0), (int, float))
        and isinstance(row.get("completion_tokens", 0), (int, float))
    )
    return {
        "successful_runs": len(successful),
        "median_seconds": median([row["seconds"] for row in successful if isinstance(row.get("seconds"), (int, float))]),
        "total_successful_seconds": round(sum(row.get("seconds", 0) for row in successful), 6),
        "total_reported_tokens": total_tokens,
        "median_prompt_tokens": median([row["prompt_tokens"] for row in successful if isinstance(row.get("prompt_tokens"), (int, float))]),
        "median_completion_tokens": median([row["completion_tokens"] for row in successful if isinstance(row.get("completion_tokens"), (int, float))]),
        "median_attempts": median([row["attempts"] for row in successful if isinstance(row.get("attempts"), (int, float))]),
        "per_task": task_summaries,
        "note": "Medians use successful, non-skipped invocations only; token totals include rows with reported prompt and completion token counts.",
    }


def run_pair(tasks, pick_command, baseline_command, timeout=60, repeats=1, repeat_delay=0):
    if not pick_command or not baseline_command:
        raise ValueError("both adapter commands are required")
    if timeout <= 0 or repeats < 1 or repeat_delay < 0:
        raise ValueError("timeout and repeats must be positive; repeat delay cannot be negative")
    pick_rows, baseline_rows = [], []
    rate_limit_hit = False
    stop_reason = "skipped because a provider rate limit was detected; no further API calls made"
    for repeat in range(repeats):
        # A delay between full rounds lets provider rolling token windows reset.
        # Do not sleep after a rate limit, since all remaining calls are skipped.
        if repeat and repeat_delay and not rate_limit_hit:
            time.sleep(repeat_delay)
        for index, task in enumerate(tasks):
            if rate_limit_hit:
                pick_rows.append(skipped_row(task, repeat + 1, stop_reason))
                baseline_rows.append(skipped_row(task, repeat + 1, stop_reason))
                continue
            # Alternate execution order to reduce systematic first/second-run bias.
            commands = (("pick", pick_command), ("baseline", baseline_command))
            if (index + repeat) % 2:
                commands = tuple(reversed(commands))
            pair = {}
            rate_limited_adapter = None
            for name, command in commands:
                row = run_one(command, task, timeout)
                pair[name] = row
                if is_provider_rate_limit(row):
                    rate_limited_adapter = name
                    rate_limit_hit = True
                    break
            for name, command in commands:
                if name not in pair:
                    pair[name] = skipped_row(
                        task, repeat + 1,
                        f"skipped because {rate_limited_adapter} adapter hit a provider rate limit; no further API calls made",
                    )
            both_succeeded = pair["pick"].get("ok") and pair["baseline"].get("ok")
            has_both_outputs = "output" in pair["pick"] and "output" in pair["baseline"]
            output_match = (
                pair["pick"]["output"] == pair["baseline"]["output"]
                if both_succeeded and has_both_outputs else None
            )
            # This is exact structural equality only, not a semantic correctness verdict.
            for name, row in pair.items():
                row["repeat"] = repeat + 1
                row["input_digest"] = hashlib.sha256(
                    json.dumps(task["input"], sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                row["output_match"] = output_match
                (pick_rows if name == "pick" else baseline_rows).append(row)
    comparable = [
        row for row in pick_rows
        if row.get("output_match") is not None
    ]
    matches = sum(row["output_match"] is True for row in comparable)
    paired_successes = sum(
        p.get("ok", False) and b.get("ok", False)
        for p, b in zip(pick_rows, baseline_rows)
    )
    return {
        "schema_version": 1,
        "tasks": [task["task"] for task in tasks],
        "repeats": repeats,
        "status": "INCONCLUSIVE_RATE_LIMIT" if rate_limit_hit else "COMPLETED",
        "rate_limit_detected": rate_limit_hit,
        "comparison": {
            "paired_successes": paired_successes,
            "output_comparable": len(comparable),
            "exact_output_matches": matches,
            "exact_output_match_rate": matches / len(comparable) if comparable else None,
            "note": "Exact structural output equality is a regression signal, not proof of semantic equivalence."
        },
        "performance_summary": {
            "pick": summarize_performance(pick_rows, tasks),
            "baseline": summarize_performance(baseline_rows, tasks),
        },
        "pick": pick_rows,
        "baseline": baseline_rows,
        "notes": [
            "Both adapters receive the same task name and input JSON.",
            "Adapter 'ok' is self-reported; configure adapters to apply the same task-specific validators.",
            "Exact output agreement is reported only when both adapters succeed and both provide an output; it is not a semantic-equivalence verdict.",
            "Seconds are measured locally per adapter invocation; provider-side billing may differ.",
            "On provider rate limiting, the benchmark stops issuing calls immediately and marks remaining rows skipped; these rows are not valid task failures.",
            f"Delay between repeat rounds: {repeat_delay} seconds.",
        ]
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, help="JSON array with task and input fields")
    parser.add_argument("--pick-command", required=True, help="adapter argv, parsed with shell-style quoting; shell is not invoked")
    parser.add_argument("--baseline-command", required=True, help="adapter argv, parsed with shell-style quoting; shell is not invoked")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--repeat-delay", type=float, default=0,
                        help="seconds to wait between repeat rounds, useful for provider rate limits")
    parser.add_argument("--write", required=True, help="output JSON file")
    args = parser.parse_args(argv)
    try:
        tasks = load_tasks(args.tasks)
        report = run_pair(tasks, shlex.split(args.pick_command), shlex.split(args.baseline_command),
                          timeout=args.timeout, repeats=args.repeats, repeat_delay=args.repeat_delay)
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        Path(args.write).write_text(output, encoding="utf-8")
        print(f"Wrote {args.write}: {len(tasks)} tasks x {args.repeats} repeats per adapter; delay={args.repeat_delay}s")
        print(f"PICK pass: {sum(bool(r['ok']) for r in report['pick'])}/{len(report['pick'])}")
        print(f"Baseline pass: {sum(bool(r['ok']) for r in report['baseline'])}/{len(report['baseline'])}")
        comparison = report["comparison"]
        rate = comparison["exact_output_match_rate"]
        rate_text = f"{rate:.1%}" if rate is not None else "n/a"
        print(
            "Exact output agreement: "
            f"{comparison['exact_output_matches']}/{comparison['output_comparable']} ({rate_text}); "
            "not a semantic-equivalence verdict"
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
