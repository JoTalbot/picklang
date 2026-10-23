"""Офлайн-приёмка: факты → черновик → ручное одобрение → заявка на review.

Никаких сетевых запросов, списаний или складских операций.
"""
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from durable_weave.pickrun import run_pick
from examples.availability_contract import checked_observer


def main():
    calls = []

    def observe(requested_id):
        calls.append("observe")
        return {"id": requested_id, "status": "unknown", "freshness": "current", "conflict_status": "none"}

    def draft(id):
        calls.append("review.draft")
        return {"id": id, "asserting": False}

    def submit(draft):
        calls.append("review.submit")
        return {"draft": draft, "manual_review_requested": True}

    def forbidden(id):
        raise AssertionError("strict path must not run for unknown")

    registry = {"inventory.observe": checked_observer(observe), "review.draft": draft,
                "strict.draft": forbidden, "review.submit": submit}
    source = Path(__file__).with_name("availability_review.pick").read_text(encoding="utf-8")
    with TemporaryDirectory() as workdir:
        args = (source, registry, {"requested_id": "item-1"}, workdir, "demo")
        result, executor = run_pick(*args)
        assert result.status == "WAITING" and calls == ["observe", "review.draft"]
        print("До одобрения:", result.status)
        executor.deliver_signal("demo", "approval", True)
        result, executor = run_pick(*args)
        assert result.status == "COMPLETED" and calls == ["observe", "review.draft", "review.submit"]
        assert executor.verify("demo")["verified"]
        print("После одобрения:", result.status)
        print(json.dumps(result.output, ensure_ascii=False))
        print("Вызовы:", calls)


if __name__ == "__main__":
    main()
