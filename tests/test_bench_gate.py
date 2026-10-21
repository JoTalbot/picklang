import copy

from bench_gate import evaluate_report


def report(pick_tokens=80, baseline_tokens=100):
    return {
        "status": "COMPLETED",
        "rate_limit_detected": False,
        "tasks": ["one", "two"],
        "repeats": 1,
        "pick": [
            {"ok": True, "output_match": True, "prompt_tokens": pick_tokens // 2, "completion_tokens": pick_tokens - pick_tokens // 2},
            {"ok": True, "output_match": True, "prompt_tokens": 0, "completion_tokens": 0},
        ],
        "baseline": [
            {"ok": True, "output_match": True, "prompt_tokens": baseline_tokens // 2, "completion_tokens": baseline_tokens - baseline_tokens // 2},
            {"ok": True, "output_match": True, "prompt_tokens": 0, "completion_tokens": 0},
        ],
    }


def test_gate_passes_only_when_all_tasks_match_and_pick_uses_fewer_tokens():
    passed, message = evaluate_report(report())
    assert passed is True
    assert "20.0% less" in message


def test_gate_rejects_no_token_savings():
    passed, message = evaluate_report(report(pick_tokens=100, baseline_tokens=100))
    assert passed is False
    assert "efficiency gate failed" in message


def test_gate_rejects_task_failure_or_output_mismatch():
    candidate = report()
    candidate["pick"][1]["ok"] = False
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "quality gate failed" in message


def test_gate_rejects_rate_limited_or_incomplete_reports():
    candidate = report()
    candidate["status"] = "INCONCLUSIVE_RATE_LIMIT"
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "INCONCLUSIVE" in message

    candidate = report()
    candidate["baseline"].pop()
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "incomplete report" in message


def test_gate_rejects_missing_usage_telemetry():
    candidate = report()
    for row in candidate["pick"] + candidate["baseline"]:
        row["prompt_tokens"] = 0
        row["completion_tokens"] = 0
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "telemetry" in message
