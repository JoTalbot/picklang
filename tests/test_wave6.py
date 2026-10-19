import asyncio
import time

import pytest

from picklang import run_program


def test_1_snapshots_resume(tmp_path):
    from durable_weave import DurableExecutor
    calls = {"n": 0}
    def inc(p):
        calls["n"] += 1
        return calls["n"]
    def wf(ctx, inp):
        out = [ctx.execute_activity("inc", {}, idempotency_key=f"k{i}") for i in range(5)]
        return out
    ex = DurableExecutor(tmp_path, {"inc": inc}, storage="sqlite", snapshot_every=2)
    r = ex.run("s1", wf, {})
    assert r.output == [1, 2, 3, 4, 5]
    ex2 = DurableExecutor(tmp_path, {"inc": inc}, storage="sqlite", snapshot_every=2)
    r2 = ex2.run("s1", wf, {})
    assert r2.output == [1, 2, 3, 4, 5] and calls["n"] == 5  # всё из журнала/снимка
    assert any(e["type"] == "StateSnapshot" for e in ex2.log.events)


def test_2_aot_matches_interpreter_and_fuses():
    from picklang.aot import run_compiled
    src = '> t(q)\nfound = web.search(q) >> top(2) >> fields("title")\nout found\n'
    reg = {"web.search": lambda q: [{"title": "a", "x": 1}, {"title": "b", "x": 2}, {"title": "c", "x": 3}]}
    from picklang.stdlib import with_stdlib
    expected, *_ = run_program(src, with_stdlib(reg), {"q": "x"})
    got, _ = run_compiled(src, with_stdlib(reg), {"q": "x"})
    assert got == expected == [{"title": "a"}, {"title": "b"}]


def test_3_group_commit_and_sampled_verify(tmp_path):
    from durable_weave.sqlite_backend import SQLiteEventLog
    log = SQLiteEventLog(tmp_path / "e.db")
    log.append_many([("A", {"i": i}) for i in range(5)])
    ok, _ = log.verify_chain()
    assert ok and log.verify_sampled(every=2)[0]


def test_4_async_executor_parallel(tmp_path):
    from durable_weave.async_executor import AsyncDurableExecutor
    async def main():
        async def slow(p):
            await asyncio.sleep(0.1)
            return p["v"]
        ex = AsyncDurableExecutor(tmp_path, {"a": slow, "b": slow})
        ex._load("e1")
        t0 = time.perf_counter()
        out = await ex.parallel([("a", {"v": 1}, "a"), ("b", {"v": 2}, "b")])
        assert out == [1, 2] and time.perf_counter() - t0 < 0.19 and ex.verify("e1")
    asyncio.run(main())


def test_5_prefetch_readonly_only():
    from picklang.prefetch import prefetch_after_wait
    calls = []
    reg = {"docs.get": lambda id: calls.append("get") or {"text": "t"},
           "mail.send": lambda u, t: calls.append("send")}
    src = '> t(x)\n@wait approval\nd = docs.get(1)\nmail.send("u", d.text)\nout d\n'
    added = prefetch_after_wait(src, reg, {"x": 1})
    assert calls == ["get"] and added["d"] == {"text": "t"}  # письмо не отправлено префетчем


def test_6_batch_loop():
    calls = {"n": 0}
    def batch(items, *a):
        calls["n"] += 1
        return [i * 10 for i in items]
    out, *_ = run_program('> t(xs)\nfor i in xs: y = ship(i)\nout y\n',
                          {"ship": lambda i: i, "ship.batch": batch}, {"xs": [1, 2, 3]})
    assert out == [10, 20, 30] and calls["n"] == 1


def test_7_cascade_escalates(monkeypatch):
    import picklang.cascade as casc
    answers = iter([("bad model", "мусор"), ("good model", '{"goal": "x", "steps": []}')])
    def fake_chat(key, model, messages, timeout=None, json_schema=None):
        _m, text = next(answers)
        return text, {"total_tokens": 10}
    monkeypatch.setattr(casc, "chat", fake_chat)
    from picklang.cascade import ModelCascade
    result = ModelCascade([["bad model"], ["good model"]]).run(
        "k", [], validator=lambda t: t.startswith("{"))
    assert result["model"] == "good model" and result["tier"] == 1


def test_8_context_prefix_stable():
    from picklang.context_compiler import compile_context
    contracts = {"bank.charge": {"args": ["u"], "returns": ["tx"]}}
    a = compile_context("задача один", contracts)
    b = compile_context("совершенно другая задача", contracts)
    assert a["prefix_digest"] == b["prefix_digest"] and a["digest"] != b["digest"]


def test_9_streaming_parser():
    from picklang.streaming import StreamingIntentParser
    p = StreamingIntentParser()
    assert p.feed("конечно! вот: ") is None and p.early_error
    p2 = StreamingIntentParser()
    assert p2.feed('{"goal": "x", "steps": [') is None and not p2.complete
    assert p2.feed(']}') == {"goal": "x", "steps": []}


def test_10_compact_serialization_cheaper():
    from picklang.serialize import compact_tokens
    data = [{"a": i, "b": f"s{i}", "c": i * 2} for i in range(10)]
    js, tsv = compact_tokens(data)
    assert tsv < js


def test_11_alias_cost(tmp_path):
    from picklang.vocab import Vocabulary
    assert Vocabulary.alias_cost("pay") >= 1
    v = Vocabulary(tmp_path)
    v.add("pay", ["u"], "x = f({u})")
    assert "pay" in v.index


def test_12_oneshot_counts():
    from bench_oneshot import TASKS  # noqa
    from picklang.parser import parse
    steps = sum(1 for t in TASKS for s in parse(t["src"]) if s.kind in ("assign", "expr"))
    assert steps > len(TASKS)  # шагов больше, чем задач: ReAct платит за каждый
