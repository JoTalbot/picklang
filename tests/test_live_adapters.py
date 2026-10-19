import json
from pathlib import Path

import pytest

from bench_live_adapters import execute_classic
from eval_live_cascade import TASKS


@pytest.mark.parametrize("name,source", [
    ("charge_tx", 'def solve(u):\n    p = bank.charge(u, 5)\n    return p["tx"]'),
    ("search_top1", 'def solve(q):\n    return [web.search(q)[0]]'),
    ("double_value", 'def solve(i):\n    return dbl(i)'),
    ("fields_a", 'def solve(x):\n    return {"a": d.get(x)["a"]}'),
    ("count_results", 'def solve(q):\n    return count(web.search(q))'),
    ("multi_step_subscribe", 'def solve(u):\n    sub = db.create_sub(u)\n    message = mail.send(u, sub["id"])\n    return {"sub": sub["id"], "message": message["id"]}'),
])
def test_classic_adapter_executes_expected_result(name, source):
    task = next(item for item in TASKS if item["name"] == name)
    assert execute_classic(source, task) == task["expected"]


def test_classic_adapter_rejects_imports_and_unlisted_attributes():
    task = next(item for item in TASKS if item["name"] == "charge_tx")
    with pytest.raises((ValueError, SyntaxError)):
        execute_classic("import os\ndef solve(u):\n    return 1", task)
    with pytest.raises(ValueError, match="attribute"):
        execute_classic("def solve(u):\n    return u.__class__", task)


def test_live_fixture_matches_canonical_inputs():
    fixture = json.loads(Path("bench/live_paired_tasks.json").read_text(encoding="utf-8"))
    assert len(fixture) == len(TASKS) == 6
    for row in fixture:
        task = next(item for item in TASKS if item["name"] == row["task"])
        assert row["input"]["inputs"] == task["inputs"]
