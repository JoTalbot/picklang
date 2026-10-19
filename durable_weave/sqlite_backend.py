"""П.13 — SQLite-бэкенд журнала событий: та же хэш-цепочка, но транзакционно.

Два процесса на одном исполнении больше не рвут JSONL-файл: append — одна
транзакция SQLite (WAL), verify_chain идентичен файловому варианту.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .cas import canonical_json, sha256_hex
from .eventlog import GENESIS, event_hash


class SQLiteEventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, type TEXT, data TEXT, prev TEXT, hash TEXT)")
        self.db.commit()
        self.events: list[dict[str, Any]] = []
        for seq, typ, data, prev, h in self.db.execute("SELECT seq,type,data,prev,hash FROM events ORDER BY seq"):
            self.events.append({"seq": seq, "type": typ, "data": json.loads(data), "prev": prev, "hash": h})

    @property
    def last_hash(self) -> str:
        return self.events[-1]["hash"] if self.events else GENESIS

    def append(self, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        event = {"seq": len(self.events), "type": type_, "data": data, "prev": self.last_hash}
        event["hash"] = event_hash(self.last_hash, event)
        with self.db:
            self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                            (event["seq"], type_, json.dumps(data, ensure_ascii=False), event["prev"], event["hash"]))
        self.events.append(event)
        return event

    def verify_chain(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, ev in enumerate(self.events):
            body = {k: v for k, v in ev.items() if k != "hash"}
            if ev.get("prev") != prev or event_hash(prev, body) != ev.get("hash"):
                return False, f"event {i}: цепочка нарушена"
            prev = ev["hash"]
        return True, "ok"
