import subprocess
import sys

import pytest

from picklang.check import check_source
from picklang.fix import fix_source, suggest_patches
from picklang.genesis import generate
from picklang.minify import compress, expand
from picklang.vocab import Vocabulary

PAYMENT = open("examples/payment.pick", encoding="utf-8").read()


# П.1 словарь проекта в CAS
def test_vocab_expands_and_dedups_in_cas(tmp_path):
    vocab = Vocabulary(tmp_path)
    body = "p = bank.charge({id}, {amt}) ! bank.refund(p.tx)\n? p.ok -> fail \"нет оплаты\""
    ref1 = vocab.add("pay", ["id", "amt"], body)
    ref2 = vocab.add("pay2", ["id", "amt"], body)
    assert ref1 == ref2 and vocab.cas.stats()["objects"] == 1  # одно тело — один объект CAS
    src = "> order(id, amt)\n&pay(id, amt)\nout p.tx\n"
    expanded = vocab.expand(src)
    assert "bank.charge(id, amt)" in expanded and vocab.verify("pay")


# П.2 ошибки готовым патчем
def test_error_as_patch():
    broken = '> order(id)\n? p.ok fail "стоп"\nout 1\n'
    patches = suggest_patches(broken)
    assert patches and patches[0]["patch"].startswith("~2:")
    fixed = fix_source(broken)
    assert '-> fail "стоп"' in fixed


def test_unknown_function_patch_suggestion():
    reg = {"bank.charge": lambda id, amt: {}}
    patches = suggest_patches('> o(id)\np = bank.charg(id, 1)\nout p\n', registry=reg)
    assert any("bank.charge" in p["patch"] for p in patches)


# П.3 статический контроль до запуска
def test_static_check_catches_undefined_and_unknown():
    diags = check_source('> o(id)\np = bank.charge(id, amt)\nout q\n',
                         registry={"bank.charge": lambda id, amt: {}})
    codes = {d["code"] for d in diags}
    assert "UNDEFINED_VAR" in codes


# П.4 генератор из намерения
def test_genesis_generates_runnable_program():
    from picklang import run_program
    src = generate({
        "goal": "order", "params": ["id", "amt"],
        "steps": [
            {"do": "bank.charge", "args": ["id", "amt"], "as": "p",
             "undo_call": "bank.refund", "undo_args": ["p.tx"]},
            {"guard": "p.ok", "fail": "отказ"},
        ],
        "out": {"tx": "p.tx"},
    })
    reg = {"bank.charge": lambda id, amt: {"ok": True, "tx": "t1"},
           "bank.refund": lambda tx: None}
    out, *_ = run_program(src, reg, {"id": 1, "amt": 5})
    assert out == {"tx": "t1"}


# П.5 сжатие идентификаторов с source-map
def test_minify_roundtrip_and_saves_on_long_names():
    src = open("bench/corpus/08_longnames.pick", encoding="utf-8").read()
    mini, mapping = compress(src)
    assert expand(mini, mapping) == src
    assert "customer_identifier" not in mini
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    assert len(enc.encode(mini)) < len(enc.encode(src))


# П.6 корпус бенчмарков существует и PICK суммарно дешевле
def test_benchmark_corpus_pick_cheaper():
    import tiktoken
    from pathlib import Path
    enc = tiktoken.get_encoding("cl100k_base")
    pick_total = py_total = 0
    for pick in Path("bench/corpus").glob("*.pick"):
        pick_total += len(enc.encode(pick.read_text(encoding="utf-8")))
        py_total += len(enc.encode(pick.with_suffix(".py").read_text(encoding="utf-8")))
    assert pick_total < py_total


# П.7 MCP: сервер становится библиотекой PICK
def test_mcp_server_becomes_pick_library():
    from picklang import run_program
    from picklang.mcp import McpClient, registry_from_mcp
    client = McpClient([sys.executable, "examples/mcp_server.py"], name="demo")
    try:
        tools = client.connect()
        assert {t["name"] for t in tools} == {"add", "upper"}
        out, *_ = run_program('> t(x)\ny = demo.add(x, 2)\nout y\n',
                              registry_from_mcp(client), {"x": 40})
        assert out == 42
    finally:
        client.close()


# П.7 Temporal: кодогенерация и (если SDK есть) настоящий класс workflow
def test_temporal_adapter():
    from durable_weave.temporal_adapter import generate_temporal_code
    code = generate_temporal_code()
    assert "class PickWorkflow" in code and "execute_activity" in code
    temporalio = pytest.importorskip("temporalio")
    from durable_weave.temporal_adapter import build_temporal_workflow
    wf, act = build_temporal_workflow()
    assert wf.__name__ == "PickWorkflow"
