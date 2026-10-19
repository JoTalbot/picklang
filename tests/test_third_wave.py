import json
import subprocess
import sys

from durable_weave.context_transport import ContextTransport
from durable_weave.slots import SlotStore
from picklang import run_program
from picklang.semcache import SemanticCache


# П.2 транспорт: контекст между процессами по digest
def test_context_between_processes(tmp_path):
    a = SlotStore(tmp_path / "A")
    a.put("task", {"q": "отчёт"})
    a.put("draft", {"text": "v2"})
    handoff = ContextTransport(tmp_path / "inbox").publish(a)
    # получатель — ОТДЕЛЬНЫЙ процесс python со своим хранилищем
    result = subprocess.run(
        [sys.executable, "examples/context_receiver.py",
         str(tmp_path / "inbox"), handoff["handoff"], str(tmp_path / "B")],
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {"task": {"q": "отчёт"}, "draft": {"text": "v2"}}


def test_context_bundle_tamper_detected(tmp_path):
    a = SlotStore(tmp_path / "A")
    a.put("x", {"v": 1})
    transport = ContextTransport(tmp_path / "inbox")
    handoff = transport.publish(a)
    bundle = tmp_path / "inbox" / f"{handoff['handoff']}.json"
    data = json.loads(bundle.read_text())
    ref = list(data["objects"])[0]
    data["objects"][ref] = {"v": 999}  # подмена содержимого бандла
    bundle.write_text(json.dumps(data))
    import pytest
    with pytest.raises(Exception):
        transport.receive(handoff["handoff"], SlotStore(tmp_path / "B"))


# П.3 семантический кэш подзадач
def test_semcache_shares_between_agents_and_skips_side_effects(tmp_path):
    calls = {"llm": 0, "mail": 0}
    cache = SemanticCache(tmp_path, namespace="prompts-v1")
    reg = cache.wrap_registry({
        "llm.plan": lambda q: calls.__setitem__("llm", calls["llm"] + 1) or {"query": q},
        "mail.send": lambda to: calls.__setitem__("mail", calls["mail"] + 1) or {"id": calls["mail"]},
    }, cacheable={"llm.plan"})
    src = '> t(q)\np = llm.plan(q)\nout p\n'
    out1, *_ = run_program(src, reg, {"q": "отчёт"})
    out2, *_ = run_program(src, reg, {"q": "отчёт"})  # второй «агент» — та же подзадача
    assert out1 == out2 == {"query": "отчёт"} and calls["llm"] == 1
    reg["mail.send"]("a@b"); reg["mail.send"]("a@b")
    assert calls["mail"] == 2  # сайд-эффект не кэшируется никогда


def test_semcache_namespace_invalidates_and_cached_run(tmp_path):
    calls = {"n": 0}
    def runner():
        calls["n"] += 1
        return {"answer": 42}
    src = '> t(x)\nout x\n'
    c1 = SemanticCache(tmp_path, namespace="prompts-v1")
    r1, hit1 = c1.cached_run(src, {"x": 1}, runner)
    r2, hit2 = c1.cached_run(src, {"x": 1}, runner)
    assert (r1, hit1, r2, hit2) == ({"answer": 42}, False, {"answer": 42}, True) and calls["n"] == 1
    c2 = SemanticCache(tmp_path, namespace="prompts-v2")  # сменили промпты — кэш мёртв
    _r, hit3 = c2.cached_run(src, {"x": 1}, runner)
    assert hit3 is False and calls["n"] == 2


def test_semcache_ttl_expires(tmp_path):
    cache = SemanticCache(tmp_path, namespace="v1", ttl_seconds=0.01)
    cache.put("llm.plan", ["q"], {"query": "q"})
    import time
    time.sleep(0.02)
    assert cache.get("llm.plan", ["q"]) is None
