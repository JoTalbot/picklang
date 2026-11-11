import pytest
from bench_ab import compare, estimate_cost, load_rows, markdown, summarize

def row(task, **updates):
    x={"task":task,"ok":True,"model":"m","attempts":1,"seconds":2.0,"prompt_tokens":100,"completion_tokens":50}
    x.update(updates); return x

def test_requires_same_task_set():
    with pytest.raises(ValueError,match="task sets differ"): compare({"a":row("a")},{"b":row("b")})

def test_metrics_and_costs():
    prices={"m":{"prompt_per_million":1.0,"completion_per_million":2.0}}
    p={"a":row("a"),"b":row("b",ok=False,attempts=2,seconds=3)}
    b={"a":row("a",prompt_tokens=200),"b":row("b",completion_tokens=300)}
    r=compare(p,b,prices,prices)
    assert r["pick"]["passed"]==1 and r["pick"]["pass_rate"]==0.5
    assert r["pick"]["attempts"]==3 and r["pick"]["total_tokens"]==300
    assert r["pick"]["estimated_cost_usd"]==pytest.approx(0.0004)
    assert r["baseline"]["estimated_cost_usd"]==pytest.approx(0.001)
    assert "Estimated API cost" in markdown(r) and "not semantic equivalence" in r["warning"]

def test_cost_unknown_without_complete_rates():
    assert estimate_cost(row("a"),None) is None
    assert estimate_cost(row("a"),{}) is None
    assert summarize({"a":row("a")})["estimated_cost_usd"] is None

def test_reject_duplicate_tasks(tmp_path):
    valid = row("a")
    f=tmp_path/"rows.json"; f.write_text(__import__("json").dumps([valid, valid]),encoding="utf-8")
    with pytest.raises(ValueError,match="duplicate task"): load_rows(f)

def test_reject_negative_metrics(tmp_path):
    f=tmp_path/"rows.json"; f.write_text('[{"task":"a","prompt_tokens":-1}]',encoding="utf-8")
    with pytest.raises(ValueError,match="non-negative"): load_rows(f)



@pytest.mark.parametrize("invalid", [True, -1, 1.5, float("nan"), float("inf"), "10", None])
def test_rejects_invalid_numeric_telemetry(tmp_path, invalid):
    import json
    f = tmp_path / "rows.json"
    item = row("a", prompt_tokens=invalid)
    f.write_text(json.dumps([item]), encoding="utf-8")
    with pytest.raises(ValueError, match="prompt_tokens"):
        load_rows(f)


def test_rejects_missing_telemetry_instead_of_assuming_zero(tmp_path):
    import json
    f = tmp_path / "rows.json"
    item = row("a")
    item.pop("completion_tokens")
    f.write_text(json.dumps([item]), encoding="utf-8")
    with pytest.raises(ValueError, match="completion_tokens"):
        load_rows(f)


@pytest.mark.parametrize("invalid", [True, -0.1, float("nan"), float("inf"), "0.1", None])
def test_rejects_invalid_pricing_rates(invalid):
    with pytest.raises(ValueError, match="invalid pricing"):
        estimate_cost(row("a"), {"m": {
            "prompt_per_million": invalid,
            "completion_per_million": 1.0,
        }})
