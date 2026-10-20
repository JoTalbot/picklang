import sys
import time

import pytest

from picklang import run_program


# П.1 кэш парсинга: повторные запуски той же программы корректны и быстры
def test_parse_cache_correct():
    src = "> t(x)\ny = f(x)\nout y\n"
    for _ in range(5):
        out, *_ = run_program(src, {"f": lambda x: x + 1}, {"x": 1})
        assert out == 2


# П.2/3 схема и параметры роутера присутствуют без сети
def test_schema_in_chat_and_router(monkeypatch):
    import picklang.live_agent as la
    captured = {}
    def fake_post(url, body, headers, timeout):
        captured["body"] = body.decode()
        return {"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 3,
                        "completion_tokens_details": {"reasoning_tokens": 2}}}
    monkeypatch.setattr(la, "_post_with_deadline", fake_post)
    from picklang.schema import intent_schema
    la.chat("k", "m", [{"role": "user", "content": "x"}], json_schema=intent_schema(),
            max_completion_tokens=100, reasoning_max_tokens=50)
    assert "json_schema" in captured["body"] and "reasoning" in captured["body"]
    from picklang.router import ModelRouter
    r = ModelRouter(["m"], reasoning_max_tokens=50)
    model, _text, _u = r.chat("k", [{"role": "user", "content": "x"}])
    assert model == "m" and r.report()["total_usage"]["reasoning_tokens"] == 2


# П.4 pushdown: сервер режет до возврата
def test_pushdown_search():
    from picklang.pushdown import pushdown_search
    search = pushdown_search(lambda q: [{"t": 1, "b": "x" * 100}, {"t": 2, "b": "y"}])
    assert search("q", limit=1, fields="t") == [{"t": 1}]


def test_mcp_search_pushdown():
    from picklang.mcp import McpClient, registry_from_mcp
    c = McpClient([sys.executable, "examples/mcp_server.py"], name="demo")
    try:
        c.connect()
        reg = registry_from_mcp(c)
        out, *_ = run_program('> t(q)\nr = demo.search(q, 2, "title")\nout r\n', reg, {"q": "x"})
        assert out == [{"title": "r0"}, {"title": "r1"}]  # body по сети не приехал
    finally:
        c.close()


# П.5 параллельный frontier durable-исполнителя
def test_durable_parallel_frontier(tmp_path):
    from durable_weave import DurableExecutor
    def slow(payload):
        time.sleep(0.15)
        return payload["v"]
    ex = DurableExecutor(tmp_path, {"a": slow, "b": slow})
    def wf(ctx, inp):
        return ctx.execute_parallel([("a", {"v": 1}, "a"), ("b", {"v": 2}, "b")])
    t0 = time.perf_counter()
    r = ex.run("p1", wf, {})
    assert r.output == [1, 2] and time.perf_counter() - t0 < 0.29
    assert ex.verify("p1")["verified"]


# П.6 пул MCP: одно соединение на много вызовов
def test_mcp_pool_reuses_connection():
    from picklang.mcp_pool import McpPool
    pool = McpPool()
    try:
        out = pool.batch([sys.executable, "examples/mcp_server.py"], "demo",
                         [("add", {"a": 1, "b": 2}), ("add", {"a": 3, "b": 4})])
        assert out == [3, 7] and pool.stats["connects"] == 1 and pool.stats["calls"] == 2
    finally:
        pool.close_all()


def test_groq_gpt_oss_uses_low_reasoning_and_current_token_limit(monkeypatch):
    import json
    import picklang.live_agent as la

    captured = {}
    monkeypatch.setattr(la, "API", "https://api.groq.com/openai/v1/chat/completions")
    def fake_post(url, body, headers, timeout):
        captured.update(json.loads(body.decode()))
        return {"choices": [{"message": {"content": "out 1"}}], "usage": {}}
    monkeypatch.setattr(la, "_post_with_deadline", fake_post)
    la.chat("k", "openai/gpt-oss-20b", [{"role": "user", "content": "x"}],
            max_completion_tokens=512)
    assert captured["max_completion_tokens"] == 512
    assert captured["reasoning_effort"] == "low"
    assert captured["include_reasoning"] is False
