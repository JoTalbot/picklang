from pathlib import Path

from bench_live_cycle import break_even_tasks, calculate_amortized_costs
from durable_weave.pickrun import run_pick
from picklang.genesis import generate
from picklang.vocab import Vocabulary

INTENT = {
    "goal": "subscribe", "params": ["user", "amt"],
    "steps": [
        {"do": "db.create_sub", "args": ["user"], "as": "sub", "undo": "db.cancel_sub(sub.id)"},
        {"use": "pay", "args": ["user", "amt"]},
        {"use": "notify", "args": ["user"]},
    ],
    "out": {"sub": "sub.id", "tx": "p.tx"},
}


def test_amortized_costs_include_one_time_vocabulary_setup():
    rows = calculate_amortized_costs(100, 20, 200)
    assert rows[0]["pick_total"] == 120
    assert rows[0]["python_total"] == 200
    assert rows[1]["pick_total"] == 300
    assert rows[1]["python_total"] == 2000
    assert rows[2]["pick_per_task"] == 21
    assert rows[2]["python_per_task"] == 200


def test_break_even_is_strict_and_handles_no_savings():
    assert break_even_tasks(100, 20, 200) == 1
    assert break_even_tasks(100, 100, 100) is None
    assert break_even_tasks(100, 120, 100) is None


def test_amortized_costs_reject_invalid_inputs():
    import pytest

    with pytest.raises(ValueError):
        calculate_amortized_costs(-1, 2, 3)
    with pytest.raises(ValueError):
        calculate_amortized_costs(1, 2, 3, task_counts=(0,))


def test_genesis_vocab_durable_crash_resume_without_double_charge(tmp_path):
    vocab = Vocabulary(tmp_path / "vocab")
    vocab.add("pay", ["user", "amt"],
              "p = bank.charge({user}, {amt}) ! bank.refund(p.tx)\n? p.ok -> fail \"отказ\"")
    vocab.add("notify", ["user"], "mail.send({user}, \"ok\")")
    src = vocab.expand(generate(INTENT))
    assert "bank.charge(user, amt)" in src

    calls = {"charge": 0, "cancel": 0}
    reg = {
        "db.create_sub": lambda user: {"id": "s1"},
        "db.cancel_sub": lambda id: calls.__setitem__("cancel", calls["cancel"] + 1),
        "bank.charge": lambda user, amt: calls.__setitem__("charge", calls["charge"] + 1) or {"ok": True, "tx": "t1"},
        "bank.refund": lambda tx: None,
        "mail.send": lambda user, text: {"id": "m1"},
    }
    workdir = tmp_path / "run"
    r1, _ = run_pick(src, reg, {"user": "u1", "amt": 10}, workdir, "sub-1", crash_after=1)
    assert r1.status == "CRASHED"
    assert calls["cancel"] == 0
    r2, ex = run_pick(src, reg, {"user": "u1", "amt": 10}, workdir, "sub-1")
    assert r2.status == "COMPLETED"
    assert calls["charge"] == 1
    assert ex.verify("sub-1")["verified"]
    assert Path("docs/LIVE_CYCLE.md").exists()
