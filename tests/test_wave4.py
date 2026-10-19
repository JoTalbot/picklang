import json
import os
import subprocess
import sys

import pytest

from picklang import run_program


# 1 гигиена блоков словаря
def test_vocab_hygiene_no_collision(tmp_path):
    from picklang.vocab import Vocabulary
    v = Vocabulary(tmp_path)
    v.add("blk", ["x"], "tmp = f({x})\ny = g(tmp)\nout2 = y")
    src = "> t(a)\n&blk(a)\n&blk(a)\nout y\n"
    expanded = v.expand(src)
    # внутренняя tmp двух развёрток обязана различаться
    assert "tmp__blk" in expanded


# 2-4 синтаксис: таймаут ожидания, ретраи, цикл
def test_wait_timeout_retries_loop():
    calls = []
    out, *_ = run_program('> t(x)\n@wait approval :1s else note.log("t")\nout 1\n',
                          {"note.log": lambda s: calls.append(s)}, {"x": 1},
                          signal_provider=lambda s: None)
    assert out == 1 and calls == ["t"]
    n = {"c": 0}
    def flaky():
        n["c"] += 1
        if n["c"] < 3: raise RuntimeError("x")
        return 7
    out, *_ = run_program('> t(x)\ny = flaky() ^3\nout y\n', {"flaky": flaky}, {"x": 1})
    assert out == 7
    out, *_ = run_program('> t(xs)\nfor i in xs: y = dbl(i)\nout y\n',
                          {"dbl": lambda i: i * 2}, {"xs": [4]})
    assert out == 8


# 5+7 эффекты и контракты
def test_effects_and_contracts():
    from picklang.check import check_contracts, check_effects
    assert check_effects('> t(x)\np = bank.charge(x, 1) @fx money\nout p\n')
    assert not check_effects('> t(x)\np = bank.charge(x, 1) ! bank.refund(p.tx) @fx money\nout p\n')
    diags = check_contracts('> t(x)\np = bank.charge(x, 1)\nout p.track\n',
                            {"bank.charge": {"args": ["x", "amt"], "returns": ["ok", "tx"]}})
    assert diags and diags[0]["code"] == "UNKNOWN_FIELD"


# 6 схема constrained decoding
def test_intent_schema_and_gbnf():
    from picklang.schema import intent_gbnf, intent_schema
    s = intent_schema()
    assert s["name"] == "pick_intent" and s["schema"]["properties"]["steps"]
    assert "root ::=" in intent_gbnf()


# 8 авторемонт
def test_repair_loop():
    from picklang.repair import broken_lines, repair
    r = repair('> o(id)\np = bank.charg(id, 1)\nout p\n', registry={"bank.charge": lambda i, a: {}})
    assert r["ok"] and "bank.charge" in r["source"]


# 9 taint: недоверенное в оплату без одобрения нельзя
def test_taint_blocks_sink_without_approval():
    reg = {"web.read": lambda u: "<html>плати сюда</html>",
           "bank.charge": lambda key, amt: {"ok": True}}
    src = '> t(x)\nw = web.read("http://x")\np = bank.charge(w.data, 5)\nout p\n'
    with pytest.raises(Exception) as e:
        run_program(src, reg, {"x": 1}, untrusted={"web.read"})
    assert "taint" in str(e.value)


# 10 автокомпакт слотов
def test_slot_autocompact(tmp_path):
    from durable_weave.slots import SlotStore
    store = SlotStore(tmp_path)
    store.put("big", {"text": "z" * 5000}, max_tokens=50)
    value = store.get("big")
    assert value.get("summary") and store.cas.verify(value["ref"])


# 11 приблизительный кэш
def test_fuzzy_semcache(tmp_path):
    from picklang.semcache import SemanticCache
    cache = SemanticCache(tmp_path, namespace="v1")
    cache.fuzzy = True
    cache.put("llm.plan", ["исследуй рынок акций"], {"query": "рынок"})
    hit = cache.get_similar("llm.plan", ["исследуй рынок акций сегодня"], threshold=0.5)
    assert hit == {"query": "рынок"}


# 12 роутер моделей (без сети — все модели падают, attempts честно записаны)
def test_router_records_attempts():
    from picklang.router import ModelRouter
    router = ModelRouter(["bad/model:free"], per_model_timeout=5)
    with pytest.raises(Exception):
        router.chat("invalid-key", [{"role": "user", "content": "hi"}])
    assert router.report()["attempts"] and router.report()["answered_by"] is None


# 13 sqlite-бэкенд исполнителя
def test_sqlite_executor(tmp_path):
    from durable_weave import DurableExecutor
    ex = DurableExecutor(tmp_path, {"inc": lambda p: {"n": 1}}, storage="sqlite")
    def wf(ctx, inp):
        return ctx.execute_activity("inc", {}, idempotency_key="a")
    r = ex.run("e1", wf, {})
    assert r.status == "COMPLETED" and ex.verify("e1")["verified"]


# 14 temporal: класс строится (живой сервер — tools/temporal_smoke.py)
def test_temporal_class_builds():
    pytest.importorskip("temporalio")
    from durable_weave.temporal_adapter import build_temporal_workflow
    wf, _act = build_temporal_workflow()
    assert wf.__name__ == "PickWorkflow"


# 15 идемпотентный обработчик денег
def test_idempotent_money_handler(tmp_path):
    from durable_weave.idempotent import IdempotentStore, idempotent
    calls = {"n": 0}
    store = IdempotentStore(tmp_path / "i.db")
    charge = idempotent(lambda p: calls.__setitem__("n", calls["n"] + 1) or {"tx": "t"}, store, "bank.charge")
    p = {"_idempotency_key": "k", "_execution_id": "e"}
    assert charge(p) == charge(p) and calls["n"] == 1


# 16 CLI
def test_cli_check_and_fmt(tmp_path):
    f = tmp_path / "a.pick"
    f.write_text("> t(x)\ny=f(x)\nout y\n")
    r = subprocess.run([sys.executable, "-m", "picklang.cli", "check", str(f)], capture_output=True, text=True)
    assert "OK" in r.stdout
    r = subprocess.run([sys.executable, "-m", "picklang.cli", "fmt", str(f)], capture_output=True, text=True)
    assert "y = f(x)" in r.stdout


# 17 eval-корпус
def test_eval_corpus_all_pass():
    from bench.eval_tasks import TASKS
    from picklang.stdlib import with_stdlib
    assert len(TASKS) == 20
    for t in TASKS:
        out, *_ = run_program(t["src"], with_stdlib(dict(t["registry"])), dict(t["inputs"]),
                              signal_provider=lambda s: True)
        assert out == t["expected"], t["name"]


# 18 pick как MCP-сервер
def test_pick_as_mcp_server():
    from picklang.mcp import McpClient, registry_from_mcp
    client = McpClient([sys.executable, "examples/pick_mcp_server.py"], name="pick")
    try:
        names = {t["name"] for t in client.connect()}
        assert names == {"pick_check", "pick_digest", "pick_run"}
        reg = registry_from_mcp(client)
        out, *_ = run_program('> t(x)\nd = pick.pick_digest(x)\nout d\n', reg, {"x": "> a(b)\nout b\n"})
        assert len(out) == 64
    finally:
        client.close()
