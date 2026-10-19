"""Append-only журнал событий исполнения с хэш-цепочкой (event sourcing).

Каждое событие содержит hash предыдущего события: правка, удаление или
перестановка записей в середине журнала ломает цепочку и ловится verify().
Итоговый digest исполнения = хэш последнего события.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cas import canonical_json, sha256_hex

GENESIS = "0" * 64


def event_hash(prev_hash: str, event_without_hash: dict[str, Any]) -> str:
    return sha256_hex(prev_hash.encode() + canonical_json(event_without_hash))


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict[str, Any]] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.events.append(json.loads(line))

    @property
    def last_hash(self) -> str:
        return self.events[-1]["hash"] if self.events else GENESIS

    def append(self, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        event = {"seq": len(self.events), "type": type_, "data": data, "prev": self.last_hash}
        event["hash"] = event_hash(self.last_hash, event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.events.append(event)
        return event

    def verify_chain(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, ev in enumerate(self.events):
            body = {k: v for k, v in ev.items() if k != "hash"}
            if ev.get("prev") != prev:
                return False, f"event {i}: prev_hash mismatch (журнал изменён/переставлен)"
            if event_hash(prev, body) != ev.get("hash"):
                return False, f"event {i}: hash mismatch (содержимое события подменено)"
            prev = ev["hash"]
        return True, "ok"
