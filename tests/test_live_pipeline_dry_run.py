"""Прогон живого конвейера без сети: подменяется только HTTP-вызов модели.

Проверяется весь путь `eval_live_cascade.main()`: сборка промптов, ModelCascade,
валидатор (check + исполнение на моках, @wait-сигналы, expect_error, after),
запись результатов и семантика `--require-all`. Ответы модели — фиксированные,
поэтому тест ловит поломки harness'а в PR, а не только при живом прогоне с ключом.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ANSWERS = {
    "charge_tx": '> t(u)\np = bank.charge(u, 5)\nout p.tx\n',
    "search_top1": '> t(q)\nr = web.search(q) >> top(1)\nout r\n',
    "double_value": '> t(i)\nr = dbl(i)\nout r\n',
    "fields_a": '> t(x)\nr = d.get(x) >> fields("a")\nout r\n',
    "count_results": '> t(q)\nn = web.search(q) >> count()\nout n\n',
    "multi_step_subscribe": '> t(u)\ns = db.create_sub(u)\nm = mail.send(u, s.id)\nout {sub: s.id, message: m.id}\n',
    "guard_pass": '> t(u, amt)\np = bank.charge(u, amt)\n? p.ok -> fail "declined"\nout p.tx\n',
    "guard_decline": '> t(u, amt)\np = bank.charge(u, amt)\n? p.ok -> fail "declined"\nout p.tx\n',
    "saga_rollback": '> t(u, amt)\np = bank.charge(u, amt) ! bank.refund(p.tx)\n? p.ok -> fail "declined"\nout p.tx\n',
    "wait_approval": '> t(user)\n@wait approval\n? approval -> fail "rejected"\nm = mail.send(user, "ok")\nout m.id\n',
    "parallel_two": '> t(x)\na = left(x) || b = right(x)\nout {a: a, b: b}\n',
    "budget_stop": '> t()\n!budget steps<1\ntick()\ntick()\nout 1\n',
}


def _run_pipeline(tmp_path, monkeypatch, answers):
    import eval_live_cascade as live
    import picklang.cascade as cascade_mod

    by_description = {task["task"]: answers[task["name"]] for task in live.TASKS}

    def scripted_chat(key, model, messages, timeout=180, json_schema=None,
                      max_completion_tokens=None, reasoning_max_tokens=None):
        prompt = messages[-1]["content"]
        for description, answer in by_description.items():
            if description in prompt:
                return answer, {"prompt_tokens": len(prompt) // 4,
                                "completion_tokens": len(answer) // 4}
        raise AssertionError(f"неизвестная задача в промпте: {prompt[-120:]}")

    monkeypatch.setattr(cascade_mod, "chat", scripted_chat)
    monkeypatch.setenv("GROQ_API_KEY", "dry-run-placeholder")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bench").mkdir()
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(sys, "argv", ["eval_live_cascade.py", "--timeout", "5", "--write", "--require-all"])
    return live.main()


def test_live_pipeline_passes_all_tasks_offline(tmp_path, monkeypatch):
    code = _run_pipeline(tmp_path, monkeypatch, ANSWERS)
    assert code == 0, "весь набор должен пройти на эталонных ответах"

    rows = json.loads((tmp_path / "bench/live_cascade_results.json").read_text(encoding="utf-8"))
    from eval_live_cascade import TASKS
    assert len(rows) == len(TASKS) == 12
    assert all(row["ok"] for row in rows), [r["task"] for r in rows if not r["ok"]]
    assert all(row["prompt_tokens"] > 0 for row in rows)

    report = (tmp_path / "docs/LIVE_CASCADE.md").read_text(encoding="utf-8")
    assert "12/12" in report


def test_live_pipeline_require_all_fails_on_bad_answer(tmp_path, monkeypatch):
    """Программа без стража не проходит guard_decline: --require-all обязан вернуть 1."""
    broken = dict(ANSWERS)
    broken["guard_decline"] = '> t(u, amt)\np = bank.charge(u, amt)\nout p.tx\n'
    code = _run_pipeline(tmp_path, monkeypatch, broken)
    assert code == 1

    rows = json.loads((tmp_path / "bench/live_cascade_results.json").read_text(encoding="utf-8"))
    failed = {row["task"] for row in rows if not row["ok"]}
    assert failed == {"guard_decline"}, failed


def test_wait_task_rejects_program_that_skips_the_wait(tmp_path, monkeypatch):
    """Обход durable-паузы мок-значением не должен засчитываться."""
    broken = dict(ANSWERS)
    broken["wait_approval"] = '> t(user)\nm = mail.send(user, "ok")\nout m.id\n'
    code = _run_pipeline(tmp_path, monkeypatch, broken)
    assert code == 1
    rows = json.loads((tmp_path / "bench/live_cascade_results.json").read_text(encoding="utf-8"))
    assert {row["task"] for row in rows if not row["ok"]} == {"wait_approval"}
