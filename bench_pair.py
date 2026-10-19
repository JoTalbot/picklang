#!/usr/bin/env python3
"""Run two JSON-over-stdin task adapters on the same fixture set and save paired results."""
import argparse
import json
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
        row = {"task": task["task"], "ok": result["ok"], "seconds": elapsed,
               "output": result.get("output")}
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


def run_pair(tasks, pick_command, baseline_command, timeout=60, repeats=1):
    if not pick_command or not baseline_command:
        raise ValueError("both adapter commands are required")
    if timeout <= 0 or repeats < 1:
        raise ValueError("timeout must be positive and repeats must be at least one")
    pick_rows, baseline_rows = [], []
    for repeat in range(repeats):
        for index, task in enumerate(tasks):
            # Alternate execution order to reduce systematic first/second-run bias.
            commands = (("pick", pick_command), ("baseline", baseline_command))
            if (index + repeat) % 2:
                commands = tuple(reversed(commands))
            pair = {}
            for name, command in commands:
                pair[name] = run_one(command, task, timeout)
            for name, row in pair.items():
                row["repeat"] = repeat + 1
                row["input_digest"] = __import__("hashlib").sha256(
                    json.dumps(task["input"], sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                (pick_rows if name == "pick" else baseline_rows).append(row)
    return {
        "schema_version": 1,
        "tasks": [task["task"] for task in tasks],
        "repeats": repeats,
        "pick": pick_rows,
        "baseline": baseline_rows,
        "notes": [
            "Both adapters receive the same task name and input JSON.",
            "Adapter 'ok' is self-reported; configure adapters to apply the same task-specific validators.",
            "Raw outputs are retained for review; exact output equality is not a substitute for semantic equivalence.",
            "Seconds are measured locally per adapter invocation; provider-side billing may differ."
        ]
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, help="JSON array with task and input fields")
    parser.add_argument("--pick-command", required=True, help="adapter argv, parsed with shell-style quoting; shell is not invoked")
    parser.add_argument("--baseline-command", required=True, help="adapter argv, parsed with shell-style quoting; shell is not invoked")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--write", required=True, help="output JSON file")
    args = parser.parse_args(argv)
    try:
        tasks = load_tasks(args.tasks)
        report = run_pair(tasks, shlex.split(args.pick_command), shlex.split(args.baseline_command),
                          timeout=args.timeout, repeats=args.repeats)
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        Path(args.write).write_text(output, encoding="utf-8")
        print(f"Wrote {args.write}: {len(tasks)} tasks x {args.repeats} repeats per adapter")
        print(f"PICK pass: {sum(bool(r['ok']) for r in report['pick'])}/{len(report['pick'])}")
        print(f"Baseline pass: {sum(bool(r['ok']) for r in report['baseline'])}/{len(report['baseline'])}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
