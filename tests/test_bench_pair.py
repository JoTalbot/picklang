import json
import sys

import pytest

from bench_pair import load_tasks, run_one, run_pair


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
