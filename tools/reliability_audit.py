#!/usr/bin/env python3
"""Адверсарный аудит надёжности durable_weave/picklang: 21 инвариант.

Проверяет процессными сценариями то, что юнит-тестам недоступно или
неудобно: подмену байта в CAS, правку журнала в SQLite и архива после
компакции, два параллельных resume одного исполнения, resume после
ротации секрета, молчащий MCP-сервер. Каждая проверка печатает PASS
(гарантия держится) или FAIL (дефект воспроизведён); exit code 1 при
любом FAIL. Сети не требует, всё во временной директории.

Это стенд, на котором были найдены дефекты PR #32; четыре регрессии из
него живут в tests/test_reliability_*.py и гоняются в CI, а полный
набор инвариантов — здесь, для ручного прогона перед релизом:
python3 tools/reliability_audit.py [--json out.json]
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from durable_weave import DurableExecutor
from durable_weave.cas import ContentAddressedStore
from durable_weave.pickrun import run_pick
from durable_weave.sqlite_backend import SQLiteEventLog
from picklang.interpreter import run_program
from picklang.result_guard import ToolResultGuard, compact_result
from picklang.stdlib import with_stdlib

RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  :: {detail}" if detail else ""))


# ---------------------------------------------------------------- A. CAS
def audit_cas(tmp: Path) -> None:
    cas = ContentAddressedStore(tmp / "cas")
    ref1 = cas.put_json({"a": 1})
    ref2 = cas.put_json({"a": 1})
    check("CAS: дедупликация одинакового содержимого", ref1 == ref2 and cas.stats()["objects"] == 1,
          f"objects={cas.stats()['objects']}")

    path = cas._path(ref1)
    path.write_bytes(b'{"a":2}')  # подмена байта
    check("CAS: подмена содержимого обнаруживается verify", cas.verify(ref1) is False)

    cas2 = ContentAddressedStore(tmp / "cas2")
    ref = cas2.put_text("secret-payload")
    cas2._path(ref).unlink()
    check("CAS: удалённый объект не проходит verify", cas2.verify(ref) is False)


# ------------------------------------------------------------- B. Journal
def audit_journal(tmp: Path) -> None:
    log = SQLiteEventLog(tmp / "events.db")
    for i in range(5):
        log.append("ActivityCompleted", {"step": f"s{i}"})
    ok, _ = log.verify_chain()
    check("Journal: чистая цепочка верифицируется", ok)

    db = sqlite3.connect(str(tmp / "events.db"))
    with db:
        db.execute("UPDATE events SET data=? WHERE seq=2", (json.dumps({"step": "tampered"}),))
    log2 = SQLiteEventLog(tmp / "events.db")
    ok, detail = log2.verify_chain()
    check("Journal: правка data в SQLite ломает цепочку", not ok, detail)

    work = tmp / "comp"
    work.mkdir()
    log3 = SQLiteEventLog(work / "e.db")
    for i in range(6):
        log3.append("ActivityCompleted", {"step": f"s{i}"})
        if i == 3:
            log3.append("StateSnapshot", {"snapshot_ref": "x", "steps": 4})
    log3.compact()
    db = sqlite3.connect(str(work / "e.db"))
    with db:
        db.execute("UPDATE archive SET data=? WHERE seq=1", (json.dumps({"step": "tampered"}),))
    log4 = SQLiteEventLog(work / "e.db")
    ok, detail = log4.verify_chain()
    check("Journal: правка архива после компакции обнаруживается", not ok, detail)


# -------------------------------------------------------------- C. Executor
def audit_executor(tmp: Path) -> None:
    ex = DurableExecutor(tmp / "ex1", {"inc": lambda p: {"n": 1}})

    def wf(ctx, inp):
        return ctx.execute_activity("inc", {}, idempotency_key="a")

    ex.run("e1", wf, {})
    info = ex.verify("e1")
    check("Executor: verify завершённого исполнения", info["verified"] and info["status"] == "COMPLETED")

    missing = ex.verify("does-not-exist")
    check("Executor: verify несуществующего исполнения НЕ должен быть verified=True",
          missing["verified"] is False,
          f"status={missing['status']} events={missing['events']} verified={missing['verified']}")

    # секрет: реплей не должен требовать переменную окружения заново
    work = tmp / "secret"
    os.environ["AUDIT_SECRET"] = "s3cr3t-value-42"
    calls = {"n": 0}
    prog = '> t(u)\nx = charge(u, "secret:AUDIT_SECRET") ! refund(x.tx)\nout x.tx\n'
    registry = {"charge": lambda u, token: (calls.__setitem__("n", calls["n"] + 1), {"ok": True, "tx": "T1"})[1],
                "refund": lambda tx: None}
    r1, _ = run_pick(prog, registry, {"u": "u1"}, work, "s-1", crash_after=1)
    del os.environ["AUDIT_SECRET"]
    r2, _ = run_pick(prog, registry, {"u": "u1"}, work, "s-1")
    check("Secrets: resume завершается без повторного чтения секрета из окружения",
          r2.status == "COMPLETED", f"first={r1.status} resume={r2.status} err={str(r2.error)[:60]}")

    leaked = []
    for path in work.rglob("*"):
        if path.is_file():
            try:
                if b"s3cr3t-value-42" in path.read_bytes():
                    leaked.append(str(path.relative_to(work)))
            except OSError:
                pass
    check("Secrets: значение не попало в журнал/CAS на диске", not leaked, ", ".join(leaked[:3]))

    # @wait: все состояния через публичный run_pick
    work2 = tmp / "signal"
    calls2 = {"n": 0}
    programs = {"send": lambda u: (calls2.__setitem__("n", calls2["n"] + 1), {"id": "m1"})[1]}
    prog = "> t(u)\n@wait approval\nm = send(u)\nout m.id\n"
    r = run_pick(prog, programs, {"u": "u1"}, work2, "w1")[0]
    check("Signals: без сигнала исполнение в WAITING", r.status == "WAITING", r.status)
    signaller = DurableExecutor(work2, programs)
    signaller.deliver_signal("w1", "approval", True)
    before = len(signaller.log.events)
    signaller.deliver_signal("w1", "approval", True)
    check("Signals: повторная доставка того же сигнала — no-op", len(signaller.log.events) == before)
    r = run_pick(prog, programs, {"u": "u1"}, work2, "w1")[0]
    check("Signals: после сигнала исполнение завершается, побочный эффект один",
          r.status == "COMPLETED" and calls2["n"] == 1, f"{r.status} calls={calls2['n']}")

    # retry политика активности
    work3 = tmp / "retry"
    attempts = {"n": 0}

    def flaky(payload):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("транзиентный сбой")
        return {"ok": True}
    ex = DurableExecutor(work3, {"flaky": flaky})

    def retry_wf(ctx, inp):
        return ctx.execute_activity("flaky", {}, idempotency_key="f", retries=2)
    r = ex.run("r1", retry_wf, {})
    check("Retries: транзиентный сбой пережит, ровно 2 попытки",
          r.status == "COMPLETED" and attempts["n"] == 2, f"attempts={attempts['n']}")


def _slow_resume_worker(workdir: str, delay: float, start_at: float) -> None:
    while time.time() < start_at:
        time.sleep(0.01)

    def handler(payload):
        time.sleep(delay)
        with open(Path(workdir) / "calls.txt", "a") as fh:
            fh.write("x\n")

    ex = DurableExecutor(Path(workdir), {"pay": handler})

    def wf(ctx, inp):
        ctx.execute_activity("pay", {"args": [1]}, idempotency_key="p1")
        return "done"

    try:
        result = ex.run("c1", wf, {})
        with open(Path(workdir) / "status.txt", "a") as fh:
            fh.write(f"{result.status}\n")
    except Exception as exc:  # noqa: BLE001
        with open(Path(workdir) / "status.txt", "a") as fh:
            fh.write(f"{type(exc).__name__}\n")


def audit_concurrency(tmp: Path) -> None:
    work = tmp / "concurrent"
    work.mkdir()
    start = time.time() + 0.3
    procs = [mp.Process(target=_slow_resume_worker, args=(str(work), 1.0, start)),
             mp.Process(target=_slow_resume_worker, args=(str(work), 1.0, start + 0.4))]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
    calls = (work / "calls.txt").read_text().count("x") if (work / "calls.txt").exists() else 0
    statuses = (work / "status.txt").read_text().split() if (work / "status.txt").exists() else []
    check("Concurrency: параллельный resume не дублирует оплату", calls <= 1,
          f"handler calls={calls} statuses={statuses}")


# ------------------------------------------------------------- D. PICK core
def audit_pick() -> None:
    reg = {"f": lambda x: {"ok": True, "v": x + 1}, "undo": lambda v: None}
    src = "> t(x)\ny = f(x) ! undo(y.v)\nout y.v\n"
    interp = run_program(src, with_stdlib(reg), {"x": 1}, prefer_aot=False)[0]
    aot = run_program(src, with_stdlib(reg), {"x": 1}, prefer_aot=True)[0]
    check("PICK: интерпретатор и AOT дают один результат", interp == aot == 2, f"{interp} vs {aot}")

    src = "> t(q)\nfound = search(q)\np = bank.charge(found.id, 5)\nout p\n"
    reg = {"search": lambda q: {"id": "x"}, "bank.charge": lambda i, a: {"ok": True}}
    try:
        run_program(src, reg, {"q": "x"}, untrusted=("search",), prefer_aot=False)
        blocked = False
    except Exception:  # noqa: BLE001
        blocked = True
    check("PICK: taint-защита блокирует недоверенные данные в bank.charge без approval", blocked)


# --------------------------------------------------------- E. Result guard
def audit_guard(tmp: Path) -> None:
    cas = ContentAddressedStore(tmp / "guard-cas")
    big = [{"title": f"t{i}", "body": "x" * 400} for i in range(50)]
    compact = compact_result(big, max_tokens=120, cas=cas)
    ok = isinstance(compact, dict) and compact.get("truncated") is True and cas.verify(compact.get("ref", ""))
    check("ResultGuard: большой результат урезан, полный лежит в CAS и проверяем", ok)

    guard = ToolResultGuard({"search": lambda q: big}, max_tokens=100, cas=cas)
    value = guard.registry["search"]("q")
    check("ResultGuard: обёртка реестра возвращает урезанное значение",
          isinstance(value, dict) and value.get("truncated") is True)


# ------------------------------------------------------------------ MCP
def audit_mcp(tmp: Path) -> None:
    try:
        from picklang.mcp import McpClient, registry_from_mcp
        client = McpClient([sys.executable, "examples/mcp_server.py"], name="demo")
        tools = client.connect()
        registry = registry_from_mcp(client)
        ok = bool(tools) and "demo.add" in registry and "demo.search" in registry
        if ok:
            out, _, _ = run_program("> t(a, b)\nr = demo.add(a, b)\nout r\n", registry, {"a": 2, "b": 3},
                                    prefer_aot=False)
            ok = out == 5
        if ok:
            found, _, _ = run_program('> t(q)\nr = demo.search(q, 3, "title")\nout r\n', registry, {"q": "x"},
                                      prefer_aot=False)
            ok = isinstance(found, list) and len(found) == 3 and set(found[0]) == {"title"}
        client.close()
        check("MCP: stdio-клиент поднимает сервер и исполняет программы PICK", ok)
    except Exception as exc:  # noqa: BLE001
        check("MCP: stdio-клиент поднимает сервер и исполняет программы PICK", False,
              f"{type(exc).__name__}: {exc}")

    # сервер, который принимает соединение и молчит: клиент не должен висеть вечно
    silent = tmp / "silent_server.py"
    silent.write_text(
        "import sys, time\n"
        "for line in sys.stdin:\n"
        "    if '\"initialize\"' in line:\n"
        "        print('{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"tools\":[]}}', flush=True)\n"
        "    time.sleep(600)\n",
        encoding="utf-8")
    try:
        from picklang.mcp import McpClient, McpError
        started = time.perf_counter()
        client = McpClient([sys.executable, str(silent)], name="silent", timeout=2)
        timed_out = False
        try:
            client.connect()
        except McpError:
            timed_out = True
        elapsed = time.perf_counter() - started
        client.close()
        check("MCP: молчащий сервер обрывается по таймауту, а не вешает процесс",
              timed_out and elapsed < 10, f"elapsed={elapsed:.1f}s")
    except (ImportError, TypeError) as exc:
        check("MCP: молчащий сервер обрывается по таймауту, а не вешает процесс", False,
              f"{type(exc).__name__}: {exc}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        audit_cas(tmp)
        audit_journal(tmp)
        audit_executor(tmp)
        audit_concurrency(tmp)
        audit_pick()
        audit_guard(tmp)
        audit_mcp(tmp)
    passed = sum(1 for r in RESULTS if r["ok"])
    print(f"\nИТОГ: {passed}/{len(RESULTS)} инвариантов держатся")
    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"записано: {out}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
