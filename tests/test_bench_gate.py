import copy

from bench_gate import evaluate_report


def report(pick_tokens=80, baseline_tokens=100):
    tasks = ["one", "two"]
    pick = []
    baseline = []
    for repeat in (1,):
        for index, task in enumerate(tasks):
            pick.append({
                "task": task, "repeat": repeat, "input_digest": f"digest-{task}",
                "model": "test-model", "ok": True, "output_match": True,
                "seconds": 0.25,
                "prompt_tokens": pick_tokens // 2 if index == 0 else 0,
                "completion_tokens": pick_tokens - pick_tokens // 2 if index == 0 else 0,
            })
            baseline.append({
                "task": task, "repeat": repeat, "input_digest": f"digest-{task}",
                "model": "test-model", "ok": True, "output_match": True,
                "seconds": 0.30,
                "prompt_tokens": baseline_tokens // 2 if index == 0 else 0,
                "completion_tokens": baseline_tokens - baseline_tokens // 2 if index == 0 else 0,
            })
    return {
        "status": "COMPLETED",
        "rate_limit_detected": False,
        "tasks": tasks,
        "repeats": 1,
        "pick": pick,
        "baseline": baseline,
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


def test_gate_rejects_misaligned_task_or_repeat():
    candidate = report()
    candidate["baseline"][0]["task"] = "two"
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "pairing integrity failed" in message

    candidate = report()
    candidate["pick"][1]["repeat"] = 2
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "pairing integrity failed" in message


def test_gate_rejects_different_inputs_or_models():
    candidate = report()
    candidate["baseline"][0]["input_digest"] = "different-input"
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "input integrity failed" in message

    candidate = report()
    candidate["baseline"][0]["model"] = "another-model"
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "model mismatch" in message



def test_gate_rejects_missing_or_unknown_report_status():
    candidate = report()
    candidate.pop("status")
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "expected COMPLETED" in message

    candidate = report()
    candidate["status"] = "PARTIAL_BUT_LOOKS_GREEN"
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "expected COMPLETED" in message


def test_gate_rejects_non_object_report():
    passed, message = evaluate_report([])
    assert passed is False
    assert "expected a JSON object" in message


def test_gate_rejects_missing_or_invalid_per_row_token_telemetry():
    candidate = report()
    candidate["pick"][0].pop("prompt_tokens")
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "invalid token telemetry" in message

    for invalid in (-1, "12", True, 1.5, None):
        candidate = report()
        candidate["baseline"][0]["completion_tokens"] = invalid
        passed, message = evaluate_report(candidate)
        assert passed is False
        assert "non-negative integer" in message


def test_gate_rejects_missing_or_blank_model_telemetry():
    for adapter in ("pick", "baseline"):
        for invalid in (None, "", "   ", 123):
            candidate = report()
            candidate[adapter][0]["model"] = invalid
            passed, message = evaluate_report(candidate)
            assert passed is False
            assert "missing model telemetry" in message


def test_gate_rejects_model_mismatch_even_when_one_model_is_missing():
    candidate = report()
    candidate["pick"][0]["model"] = ""
    candidate["baseline"][0]["model"] = "test-model"
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "missing model telemetry" in message


def test_gate_rejects_missing_or_invalid_duration_telemetry():
    for invalid in (None, -0.01, "0.2", True, float("nan"), float("inf")):
        candidate = report()
        candidate["pick"][0]["seconds"] = invalid
        passed, message = evaluate_report(candidate)
        assert passed is False
        assert "invalid duration telemetry" in message

    candidate = report()
    candidate["baseline"][0].pop("seconds")
    passed, message = evaluate_report(candidate)
    assert passed is False
    assert "invalid duration telemetry" in message
