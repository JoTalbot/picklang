import tempfile
from pathlib import Path

import pytest

from picklang import run_program


def test_1_mine_patterns():
    from picklang.traces_mine import mine_patterns
    traces = [["stock.reserve", "bank.charge", "mail.send"]] * 4
    props = mine_patterns(traces)
    assert props and props[0]["count"] == 4 and props[0]["tokens_saved_est"] > 0


def test_2_skills_only_verified(tmp_path):
    from picklang.skills import SkillLibrary
    lib = SkillLibrary(tmp_path)
    src = "> pay(u)\nout u\n"
    with pytest.raises(ValueError):
        lib.register("pay", src, {"u": 1}, 1, verified=False)
    lib.register("pay", src, {"u": 1}, 1, verified=True)
    assert lib.find("pay")["source"] == src


def test_3_memory_consolidation():
    from picklang.memory import consolidate
    lessons = consolidate([{"tool": "bank.charge", "ok": False, "error": "timeout"}] * 3)
    assert "падает 3/3" in lessons[0]["lesson"]


def test_4_tool_rag_and_5_context_compiler():
    from picklang.context_compiler import compile_context
    from picklang.toolrag import select_tools
    contracts = {"bank.charge": {"args": ["u"], "returns": ["tx"], "description": "оплата списать деньги"},
                 "web.search": {"args": ["q"], "returns": [], "description": "поиск интернет"}}
    sel = select_tools("списать оплату", contracts, top_k=1)
    assert sel["names"] == ["bank.charge"] and sel["tokens_selected"] < sel["tokens_all"]
    ctx = compile_context("списать оплату", sel["selected"])
    assert "bank.charge" in ctx["prompt"] and len(ctx["digest"]) == 64


def test_6_contract_drift(tmp_path):
    from picklang.drift import affected_programs, compare_contracts
    old = {"bank.charge": {"returns": ["ok", "tx"]}}
    new = {"bank.charge": {"returns": ["ok", "transaction_id"]}}
    diff = compare_contracts(old, new)
    assert diff["breaking"]
    affected = affected_programs(diff, {"prog1": "p = bank.charge(u, 1)\nout p.tx\n"})
    assert affected and "transaction_id" in affected[0]["suggestion"]


def test_7_tool_stats():
    from durable_weave.tool_stats import ToolStats
    stats = ToolStats()
    reg = stats.wrap_registry({"f": lambda x: x + 1})
    reg["f"](1); reg["f"](2)
    assert stats.summary()["f"]["calls"] == 2 and "f:" in stats.planner_table()


def test_8_speculation_sandbox(tmp_path):
    from picklang.speculation import FixtureStore, evaluate_branch
    store = FixtureStore(tmp_path / "fx.json")
    rec = store.recording_registry({"bank.charge": lambda u, a: {"ok": True, "tx": "t1"}})
    rec["bank.charge"]("u", 5)
    store2 = FixtureStore(tmp_path / "fx.json")
    result = evaluate_branch('> t(u)\np = bank.charge(u, 5)\nout p.tx\n',
                             store2.replay_registry(["bank.charge"]), {"u": "u"})
    assert result["ok"] and result["output"] == "t1"
    miss = evaluate_branch('> t(u)\np = bank.charge(u, 9)\nout p.tx\n',
                           store2.replay_registry(["bank.charge"]), {"u": "u"})
    assert not miss["ok"]  # песочница в живую не ходит


def test_9_escalation():
    from picklang.escalation import decide, human_summary
    assert decide(amount=10, confidence=0.95)["action"] == "auto"
    assert decide(amount=5000, irreversible=True)["action"] == "approve"
    assert "Собираюсь" in human_summary("списать 5000", decide(amount=5000, irreversible=True))


def test_10_proof_of_safety():
    from picklang.proof import prove_safety
    assert not prove_safety('> t(x)\np = bank.charge(x, 1)\nout p\n')["safe"]
    assert prove_safety('> t(x)\n@wait approval\n? approval -> fail "нет"\np = bank.charge(x, 1)\nout p\n')["safe"]


def test_11_adversarial_corpus_present():
    from bench.adversarial import CASES
    assert len(CASES) == 5


def test_12_attribution(tmp_path):
    from durable_weave import DurableExecutor
    from picklang.attribution import attribute
    ex = DurableExecutor(tmp_path, {"big": lambda p: {"data": "z" * 100}, "small": lambda p: 1})
    def wf(ctx, inp):
        a = ctx.execute_activity("big", {}, idempotency_key="a")
        return ctx.execute_activity("small", {}, idempotency_key="b")
    ex.run("e1", wf, {})
    sizes = {}
    for ev in ex.log.events:
        ref = ev["data"].get("result_ref")
        if ref:
            sizes[ref] = len(ex.cas.get_bytes(ref))
    report = attribute(ex.log.events, sizes)
    assert report["top"]["activity"] == "big"
