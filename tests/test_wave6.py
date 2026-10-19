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
    def fake_chat(key, model, messages, timeout=None, json_schema=None, **kw):
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


def test_aot_loop_parallel_parity():
    from picklang.aot import run_compiled
    src = "> t(xs)\nfor i in xs: y = dbl(i)\na = f(1) || b = g(2)\ndone when: y > 1\nout {y: y, a: a}\n"
    reg = {"dbl": lambda i: i * 2, "f": lambda x: 10, "g": lambda x: 20}
    out, _ = run_compiled(src, reg, {"xs": [1, 3]})
    assert out == {"y": 6, "a": 10}


def test_sqlite_compaction_resume_still_verifies(tmp_path):
    from durable_weave import DurableExecutor
    ex = DurableExecutor(tmp_path, {"inc": lambda p: {"n": 1}}, storage="sqlite", snapshot_every=5)
    def wf(ctx, inp):
        return [ctx.execute_activity("inc", {}, idempotency_key=f"k{i}") for i in range(12)]
    ex.run("c1", wf, {})
    info = ex.log.compact()
    assert info["archived"] > 0
    ex2 = DurableExecutor(tmp_path, {"inc": lambda p: {"n": 1}}, storage="sqlite", snapshot_every=5)
    r = ex2.run("c1", wf, {})
    assert r.status == "COMPLETED" and ex2.verify("c1")["verified"]


def test_auto_aot_fast_path_parity():
    src = "> t(x)\ny = f(x)\nout y\n"
    reg = {"f": lambda x: x + 1}
    assert run_program(src, reg, {"x": 1})[0] == run_program(src, reg, {"x": 1}, prefer_aot=False)[0] == 2
    # с компенсацией быстрый путь запрещён — работает интерпретатор с сагой
    src2 = "> t(x)\ny = f(x) ! undo(y)\nout y\n"
    reg2 = {"f": lambda x: 5, "undo": lambda y: None}
    assert run_program(src2, reg2, {"x": 1})[0] == 5


def test_aot_batch_loop_and_donewhen_via_fast_path():
    calls = {"batch": 0}
    reg = {"ship": lambda i: i * 10, "ship.batch": lambda xs: [x * 10 for x in xs]}
    orig = reg["ship.batch"]
    def batch(xs):
        calls["batch"] += 1
        return orig(xs)
    reg["ship.batch"] = batch
    out, _, _ = run_program("> t(xs)\nfor i in xs: y = ship(i)\nout y\n", reg, {"xs": [1, 2, 3]})
    assert out == [10, 20, 30] and calls["batch"] == 1
    from picklang.interpreter import TaskNotDone
    try:
        run_program("> t(x)\ny = f(x)\ndone when: y > 5\nout y\n", {"f": lambda x: 1}, {"x": 1})
        raise AssertionError("ждался TaskNotDone")
    except TaskNotDone:
        pass


def test_chat_total_deadline_against_dripping_server():
    import http.server
    import threading
    import time as _time

    from picklang import live_agent

    class Drip(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = b'{"choices": [{"message": {"content": "x"}}], "usage": {}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for i in range(0, len(body), 4):  # капаем по 4 байта раз в 0.25 с: полный ответ ~4 с
                try:
                    self.wfile.write(body[i:i + 4]); self.wfile.flush()
                except BrokenPipeError:
                    break
                _time.sleep(0.25)
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    old = live_agent.API
    live_agent.API = f"http://127.0.0.1:{srv.server_address[1]}/api"
    try:
        t0 = _time.monotonic()
        try:
            live_agent.chat("k", "m", [{"role": "user", "content": "hi"}], timeout=1)
            raise AssertionError("ждался дедлайн")
        except live_agent.LLMError as e:
            assert "Timeout" in str(e) or "дедлайн" in str(e)
        assert _time.monotonic() - t0 < 3.0  # без общего дедлайна тут было бы ~4+ с
    finally:
        live_agent.API = old
        srv.shutdown()
