import json
import sys

import pytest

import bench_pair
from bench_pair import is_provider_rate_limit, load_tasks, run_one, run_pair


def adapter(code):
    return [sys.executable, "-c", code]


def test_same_inputs_are_sent_to_both_adapters_and_order_alternates(tmp_path):
    tasks = [
        {"task": "one", "input": {"value": 1}},
        {"task": "two", "input": {"value": 2}},
    ]
    code = (
        "import json,sys; x=json.loads(sys.stdin.readline()); "
        "print(json.dumps({'ok': True, 'output': x['input']}))"
    )
    report = run_pair(tasks, adapter(code), adapter(code), repeats=2)
    assert len(report["pick"]) == len(report["baseline"]) == 4
    assert all(row["ok"] for row in report["pick"] + report["baseline"])
    assert {r["input_digest"] for r in report["pick"]} == {
        r["input_digest"] for r in report["baseline"]
    }
    assert [r["repeat"] for r in report["pick"]] == [1, 1, 2, 2]
    assert all(row["output_match"] is True for row in report["pick"] + report["baseline"])
    assert report["comparison"]["paired_successes"] == 4
    assert report["comparison"]["output_comparable"] == 4
    assert report["comparison"]["exact_output_matches"] == 4
    assert report["comparison"]["exact_output_match_rate"] == 1.0



def test_repeated_runs_report_medians_and_token_totals():
    tasks = [{"task": "telemetry", "input": {"value": 1}}]
    code = (
        "import json; print(json.dumps({'ok': True, 'output': {'value': 1}, "
        "'prompt_tokens': 10, 'completion_tokens': 2, 'attempts': 1}))"
    )
    report = run_pair(tasks, adapter(code), adapter(code), repeats=3)
    summary = report["performance_summary"]
    assert summary["pick"]["successful_runs"] == 3
    assert summary["baseline"]["successful_runs"] == 3
    assert summary["pick"]["total_reported_tokens"] == 36
    assert summary["baseline"]["total_reported_tokens"] == 36
    assert summary["pick"]["median_prompt_tokens"] == 10
    assert summary["pick"]["median_completion_tokens"] == 2
    assert summary["pick"]["median_attempts"] == 1
    assert summary["pick"]["per_task"][0]["successful_runs"] == 3
    assert isinstance(summary["pick"]["median_seconds"], (int, float))
    assert "successful" in summary["pick"]["note"]



def test_output_mismatch_is_reported_without_claiming_semantic_failure():
    tasks = [{"task": "different", "input": {"value": 1}}]
    pick = adapter("import json; print(json.dumps({'ok': True, 'output': {'value': 1}}))")
    baseline = adapter("import json; print(json.dumps({'ok': True, 'output': {'value': 2}}))")
    report = run_pair(tasks, pick, baseline)
    assert report["pick"][0]["ok"] is True
    assert report["baseline"][0]["ok"] is True
    assert report["pick"][0]["output_match"] is False
    assert report["baseline"][0]["output_match"] is False
    assert report["comparison"]["exact_output_matches"] == 0
    assert report["comparison"]["exact_output_match_rate"] == 0.0
    assert "not proof" in report["comparison"]["note"]


def test_output_comparison_is_unknown_when_adapter_omits_output():
    tasks = [{"task": "no-output", "input": {}}]
    code = "import json; print(json.dumps({'ok': True}))"
    report = run_pair(tasks, adapter(code), adapter(code))
    assert report["pick"][0]["output_match"] is None
    assert report["baseline"][0]["output_match"] is None
    assert report["comparison"]["output_comparable"] == 0
    assert report["comparison"]["exact_output_match_rate"] is None


def test_timeout_is_a_failed_row():
    task = {"task": "slow", "input": {}}
    row = run_one(adapter("import time; time.sleep(1)"), task, timeout=0.05)
    assert row["ok"] is False
    assert "timed out" in row["error"]


def test_baseline_succeeds_when_given_a_realistic_timeout():
    tasks = [{"task": "fast", "input": {}}]
    valid_json = "import json; print(json.dumps({'ok': True}))"
    report = run_pair(
        tasks,
        adapter(valid_json),
        adapter(valid_json),
        timeout=5,
    )
    assert report["pick"][0]["ok"] is True
    assert report["baseline"][0]["ok"] is True


def test_invalid_adapter_output_is_failed_not_crash():
    tasks = [{"task": "bad", "input": {}}]
    report = run_pair(
        tasks,
        adapter("print('not json')"),
        adapter("import json; print(json.dumps({'ok': True}))"),
    )
    assert report["pick"][0]["ok"] is False
    assert "not one JSON object" in report["pick"][0]["error"]
    assert report["pick"][0]["output_match"] is None


def test_load_tasks_rejects_duplicate_names(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([
        {"task": "same", "input": 1},
        {"task": "same", "input": 2},
    ]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_tasks(path)


def test_provider_rate_limit_stops_all_remaining_calls(tmp_path):
    marker = tmp_path / "baseline-called"
    rate_limited = adapter(
        "import json; print(json.dumps({'ok': False, 'error': 'provider rate limit HTTP 429'}))"
    )
    baseline = adapter(
        "from pathlib import Path; import json,sys; "
        + "Path(" + repr(str(marker)) + ").write_text('called'); "
        + "print(json.dumps({'ok': True}))"
    )
    tasks = [
        {"task": "one", "input": {"value": 1}},
        {"task": "two", "input": {"value": 2}},
    ]
    report = run_pair(tasks, rate_limited, baseline, repeats=2)

    assert report["status"] == "INCONCLUSIVE_RATE_LIMIT"
    assert report["rate_limit_detected"] is True
    assert len(report["pick"]) == len(report["baseline"]) == 4
    assert "429" in report["pick"][0]["error"]
    assert report["baseline"][0]["skipped"] is True
    assert all(row.get("skipped") for row in report["pick"][1:] + report["baseline"][1:])
    assert not marker.exists()
    assert report["comparison"]["output_comparable"] == 0
    assert report["comparison"]["exact_output_match_rate"] is None


def test_rate_limit_detector_does_not_match_429_inside_decimal_telemetry():
    assert not is_provider_rate_limit({"error": "completion_time': 0.05942919"})
    assert not is_provider_rate_limit({"error": "normal failure after 0.123429 seconds"})
    assert is_provider_rate_limit({"error": "provider returned HTTP 429 Too Many Requests"})
    assert is_provider_rate_limit({"error": "rate_limit_exceeded"})


def test_repeat_delay_waits_between_rounds_only(monkeypatch):
    tasks = [{"task": "one", "input": {"value": 1}}]
    code = "import json; print(json.dumps({'ok': True, 'output': {'value': 1}}))"
    sleeps = []
    monkeypatch.setattr(bench_pair, "_sleep", lambda seconds: sleeps.append(seconds))
    report = run_pair(tasks, adapter(code), adapter(code), repeats=3, repeat_delay=65)
    assert report["status"] == "COMPLETED"
    assert sleeps == [65, 65]
    assert report["repeats"] == 3
