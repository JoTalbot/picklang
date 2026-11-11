import os

import pytest

from durable_weave.slots import SlotStore
from picklang import run_program
from picklang.interpreter import BudgetExceeded, TaskNotDone
from picklang.result_guard import ToolResultGuard, estimate_tokens
from picklang.status import status_protocol
from picklang.stdlib import with_stdlib


# П.1 проекции и лимиты результатов
def test_projection_cuts_tool_output_before_context():
    big = [{"title": f"t{i}", "url": f"u{i}", "body": "x" * 200} for i in range(20)]
    reg = with_stdlib({"web.search": lambda q: big})
    out, env, _ = run_program(
        '> t(q)\nfound = web.search(q) >> top(2) >> fields("title")\nout found\n', reg, {"q": "x"})
    assert out == [{"title": "t0"}, {"title": "t1"}]
    assert estimate_tokens(out) < estimate_tokens(big) / 10


def test_result_guard_truncates_with_cas_ref(tmp_path):
    from durable_weave import ContentAddressedStore
    cas = ContentAddressedStore(tmp_path)
    guard = ToolResultGuard({"web.search": lambda q: [{"body": "y" * 1000}] * 10},
                            max_tokens=50, cas=cas)
    result = guard.registry["web.search"]("q")
    assert result["truncated"] and cas.verify(result["ref"])
    assert guard.stats["tokens_saved"] > 0


# П.3 бюджет
def test_budget_stops_agent_loop():
    reg = {"f": lambda: 1}
    with pytest.raises(BudgetExceeded):
        run_program('> t(x)\n!budget steps<2\na = f()\nb = f()\nc = f()\nout c\n', reg, {"x": 1})


# П.4 компактный статус
def test_status_protocol_one_line_per_step():
    proto = status_protocol(["bank.charge", "mail.send"], [{"tx": "1"}, {"id": "2"}], remaining=["notify"])
    lines = proto.splitlines()
    assert lines[0].startswith("ok 1 bank.charge d=") and lines[-1].startswith("осталось:")


# П.5 task card: done when
def test_done_when_blocks_false_victory():
    reg = {"mail.send": lambda to: {"id": None}}
    src = '> t(x)\nsent = mail.send(x)\ndone when: sent.id\nout sent\n'
    with pytest.raises(TaskNotDone):
        run_program(src, reg, {"x": "a@b"})
    reg2 = {"mail.send": lambda to: {"id": "m1"}}
    out, *_ = run_program(src, reg2, {"x": "a@b"})
    assert out == {"id": "m1"}


# П.6 capabilities, секреты, недоверенные результаты
def test_capabilities_secrets_untrusted(monkeypatch):
    monkeypatch.setenv("BANK_KEY", "sk-live-123")
    seen = {}
    reg = {"bank.charge": lambda key, amt: seen.update(key=key) or {"ok": True},
           "web.read": lambda url: "подозрительный текст страницы",
           "evil.fn": lambda: 1}
    src = '> t(x)\np = bank.charge("secret:BANK_KEY", 10)\nw = web.read("http://x")\nout {p: p, w: w}\n'
    out, *_ = run_program(src, reg, {"x": 1}, capabilities={"bank.charge", "web.read"},
                          untrusted={"web.read"})
    assert seen["key"] == "sk-live-123"  # секрет подставлен рантаймом, в программе его нет
    assert out["w"] == {"data": "подозрительный текст страницы", "untrusted": True}
    with pytest.raises(Exception):
        run_program('> t(x)\ny = evil.fn()\nout y\n', reg, {"x": 1}, capabilities={"bank.charge"})


# П.2 слоты контекста и diff
def test_slots_slice_and_diff(tmp_path):
    store = SlotStore(tmp_path)
    store.put("task", {"q": "отчёт"})
    store.put("draft", {"text": "v1"})
    before = store.snapshot()
    store.put("draft", {"text": "v2"})
    store.put("sent", {"id": "m1"})
    after = store.snapshot()
    assert store.slice(["task"]) == {"task": {"q": "отчёт"}}  # субагент получает срез, а не всё
    diff = SlotStore.diff(before, after)
    assert set(diff) == {"draft", "sent"} and diff["draft"] == {"changed": {"text": "v2"}}


# П.7 параллельные ветви
def test_parallel_branches_merge():
    reg = {"research": lambda q: ["r1"], "prices": lambda q: [10, 20]}
    out, env, _ = run_program('> t(q)\na = research(q) || b = prices(q)\nout {a: a, b: b}\n', reg, {"q": "x"})
    assert out == {"a": ["r1"], "b": [10, 20]}


@pytest.mark.parametrize("value", [
    {"payload": {"nested": "x" * 5000}, "other": "y" * 2000},
    "z" * 10000,
    [{"payload": "q" * 3000}] * 20,
])
def test_result_guard_enforces_budget_for_all_shapes(value):
    from picklang.result_guard import compact_result

    limit = 50
    result = compact_result(value, max_tokens=limit)
    assert result["truncated"] is True
    assert estimate_tokens(result) <= limit


def test_result_guard_handles_tiny_budget_and_rejects_invalid_budget():
    from picklang.result_guard import compact_result

    assert estimate_tokens(compact_result({"huge": "x" * 1000}, max_tokens=10)) <= 10
    with pytest.raises(ValueError, match="max_tokens"):
        compact_result("value", max_tokens=0)


def test_agent_tool_benchmark_reports_precise_fixture_metrics():
    from bench_agent_tools import RESULTS, projection_metrics

    metrics = projection_metrics(RESULTS)
    assert metrics["raw_tokens"] > metrics["projected_tokens"] > 0
    assert metrics["ratio"] == pytest.approx(
        metrics["raw_tokens"] / metrics["projected_tokens"]
    )
    assert 0 < metrics["saved_percent"] < 100

def test_batch_loop_cannot_bypass_capability_allowlist():
    calls = []
    registry = {
        "ship": lambda item: calls.append(("single", item)) or item,
        "ship.batch": lambda items: calls.append(("batch", list(items))) or items,
    }
    with pytest.raises(Exception, match="capability"):
        run_program(
            "> t(xs)\nfor i in xs: y = ship(i)\nout y\n",
            registry,
            {"xs": [1, 2]},
            capabilities={"other"},
            prefer_aot=False,
        )
    assert calls == []


def test_batch_loop_preserves_untrusted_taint_at_sinks():
    calls = []
    registry = {
        "web.read": lambda item: item,
        "web.read.batch": lambda items: calls.append("batch") or list(items),
        "bank.charge": lambda payload, amount: calls.append("charge") or {"ok": True},
    }
    source = (
        "> t(xs)\n"
        "for i in xs: page = web.read(i)\n"
        "charge = bank.charge(page.data, 1)\n"
        "out charge\n"
    )
    from picklang.interpreter import PickRuntimeError
    with pytest.raises(PickRuntimeError, match="taint"):
        run_program(source, registry, {"xs": ["u1", "u2"]},
                    untrusted={"web.read"}, prefer_aot=False)
    assert calls == ["batch"]


def test_batch_loop_charges_result_token_budget():
    calls = []
    registry = {
        "ship": lambda item: item,
        "ship.batch": lambda items: calls.append("batch") or [item * 10 for item in items],
    }
    source = (
        "> t(xs)\n"
        "!budget tokens<1\n"
        "for i in xs: y = ship(i)\n"
        "out y\n"
    )
    with pytest.raises(BudgetExceeded):
        run_program(source, registry, {"xs": [1, 2, 3]}, prefer_aot=False)
    assert calls == ["batch"]


def test_empty_batch_loop_matches_zero_iteration_semantics():
    calls = []
    registry = {
        "ship": lambda item: item,
        "ship.batch": lambda items: calls.append("batch") or list(items),
    }
    with pytest.raises(Exception, match="не определена"):
        run_program(
            "> t(xs)\nfor i in xs: y = ship(i)\nout y\n",
            registry,
            {"xs": []},
            prefer_aot=False,
        )
    assert calls == []


def test_batch_loop_does_not_skip_saga_compensations():
    calls = []
    registry = {
        "ship": lambda item: calls.append(("ship", item)) or item,
        "ship.batch": lambda items: calls.append(("batch", list(items))) or list(items),
        "release": lambda item: calls.append(("release", item)),
    }
    source = (
        "> t(xs)\n"
        "for i in xs: y = ship(i) ! release(i)\n"
        "? false -> fail \"force rollback\"\n"
        "out y\n"
    )
    from picklang.interpreter import PickRuntimeError
    with pytest.raises(PickRuntimeError, match="guard failed"):
        run_program(source, registry, {"xs": [1, 2]}, prefer_aot=False)
    assert calls == [
        ("ship", 1), ("ship", 2), ("release", 2), ("release", 1)
    ]


def test_parallel_untrusted_result_remains_tainted():
    calls = []
    registry = {
        "web.read": lambda url: "untrusted content",
        "other": lambda: "safe",
        "bank.charge": lambda payload, amount: calls.append("charge") or {"ok": True},
    }
    source = (
        "> t(url)\n"
        "page = web.read(url) || safe = other()\n"
        "charge = bank.charge(page.data, 1)\n"
        "out charge\n"
    )
    from picklang.interpreter import PickRuntimeError
    with pytest.raises(PickRuntimeError, match="taint"):
        run_program(source, registry, {"url": "u"},
                    untrusted={"web.read"}, prefer_aot=False)
    assert calls == []


@pytest.mark.parametrize("approval", [1, "true", "approved"])
def test_truthy_non_boolean_approval_does_not_release_untrusted_sink(approval):
    calls = []
    registry = {
        "web.read": lambda url: "untrusted content",
        "bank.charge": lambda payload, amount: calls.append("charge") or {"ok": True},
    }
    source = (
        "> t(url)\n"
        "page = web.read(url)\n"
        "charge = bank.charge(page.data, 1)\n"
        "out charge\n"
    )
    from picklang.interpreter import PickRuntimeError
    with pytest.raises(PickRuntimeError, match="taint"):
        run_program(source, registry, {"url": "u", "approval": approval},
                    untrusted={"web.read"}, prefer_aot=False)
    assert calls == []


def test_boolean_true_approval_releases_untrusted_sink():
    calls = []
    registry = {
        "web.read": lambda url: "untrusted content",
        "bank.charge": lambda payload, amount: calls.append("charge") or {"ok": True},
    }
    source = (
        "> t(url)\n"
        "page = web.read(url)\n"
        "charge = bank.charge(page.data, 1)\n"
        "out charge\n"
    )
    out, *_ = run_program(source, registry, {"url": "u", "approval": True},
                          untrusted={"web.read"}, prefer_aot=False)
    assert out == {"ok": True}
    assert calls == ["charge"]



def test_truthy_non_boolean_wait_signal_does_not_approve_tainted_sink():
    calls = []
    registry = {
        "web.read": lambda url: "untrusted content",
        "bank.charge": lambda payload, amount: calls.append("charge") or {"ok": True},
    }
    source = (
        "> t(url)\n"
        "@wait approval\n"
        "page = web.read(url)\n"
        "charge = bank.charge(page.data, 1)\n"
        "out charge\n"
    )
    from picklang.interpreter import PickRuntimeError
    with pytest.raises(PickRuntimeError, match="taint"):
        run_program(source, registry, {"url": "u"},
                    signal_provider=lambda _name: 1,
                    untrusted={"web.read"}, prefer_aot=False)
    assert calls == []
