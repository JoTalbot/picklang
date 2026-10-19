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

    def append_many(self, items: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        """Групповой коммит: пачка событий одной транзакцией (один fsync на пачку)."""
        out = []
        with self.db:
            for type_, data in items:
                event = {"seq": len(self.events), "type": type_, "data": data, "prev": self.last_hash}
                event["hash"] = event_hash(self.last_hash, event)
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                                (event["seq"], type_, json.dumps(data, ensure_ascii=False), event["prev"], event["hash"]))
                self.events.append(event)
                out.append(event)
        return out

    def verify_sampled(self, every: int = 10) -> tuple[bool, str]:
        """Ленивая проверка: края и каждое N-е событие вместо полной цепочки."""
        if not self.events:
            return True, "empty"
        idx = sorted(set([0, len(self.events) - 1] + list(range(0, len(self.events), every))))
        prev_map = {ev["seq"]: ev for ev in self.events}
        for i in idx:
            ev = self.events[i]
            body = {k: v for k, v in ev.items() if k != "hash"}
            prev_hash = GENESIS if i == 0 else self.events[i - 1]["hash"]
            if ev.get("prev") != prev_hash or event_hash(prev_hash, body) != ev.get("hash"):
                return False, f"event {i}: нарушение на выборке"
        return True, f"sampled ok ({len(idx)}/{len(self.events)})"

    def verify_chain(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, ev in enumerate(self.events):
            body = {k: v for k, v in ev.items() if k != "hash"}
            if ev.get("prev") != prev or event_hash(prev, body) != ev.get("hash"):
                return False, f"event {i}: цепочка нарушена"
            prev = ev["hash"]
        return True, "ok"
