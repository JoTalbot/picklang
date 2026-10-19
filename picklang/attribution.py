"""П.12 — Атрибуция расхода по шагам исполнения.

По журналу durable-исполнителя: какой шаг съел бюджет (события, артефакты CAS),
а не общее «было дорого». Топ-пожиратель — первой строкой.
"""
from __future__ import annotations

from typing import Any


def attribute(events: list[dict], cas_sizes: dict[str, int] | None = None) -> dict[str, Any]:
    per_step: list[dict[str, Any]] = []
    total_bytes = 0
    for ev in events:
        if ev["type"] != "ActivityCompleted":
            continue
        ref = ev["data"].get("result_ref", "")
        size = (cas_sizes or {}).get(ref, 0)
        total_bytes += size
        per_step.append({"step": ev["data"]["step"], "activity": ev["data"]["activity"],
                         "artifact_bytes": size})
    per_step.sort(key=lambda s: -s["artifact_bytes"])
    return {"steps": per_step, "total_artifact_bytes": total_bytes,
            "top": per_step[0] if per_step else None,
            "events": len(events)}
