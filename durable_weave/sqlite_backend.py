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

GENESIS = "0" * 64


def event_hash(prev_hash: str, event_without_hash: dict[str, Any]) -> str:
    return sha256_hex(prev_hash.encode() + canonical_json(event_without_hash))


class SQLiteEventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.execute("PRAGMA journal_mode=WAL")
        # WAL + NORMAL: процесс может умереть без потери коммитов, fsync на каждый
        # коммит не делается (риск — только смерть ОС/питания, это честный обмен)
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, type TEXT, data TEXT, prev TEXT, hash TEXT)")
        self.db.commit()
        self.events: list[dict[str, Any]] = []
        for seq, typ, data, prev, h in self.db.execute("SELECT seq,type,data,prev,hash FROM events ORDER BY seq"):
            self.events.append({"seq": seq, "type": typ, "data": json.loads(data), "prev": prev, "hash": h})

    @property
    def last_hash(self) -> str:
        return self.events[-1]["hash"] if self.events else GENESIS

    def append(self, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        # seq — не длина списка: после компакции список короче, а номера обязаны расти
        next_seq = self.events[-1]["seq"] + 1 if self.events else 0
        event = {"seq": next_seq, "type": type_, "data": data, "prev": self.last_hash}
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
                next_seq = self.events[-1]["seq"] + 1 if self.events else 0
                event = {"seq": next_seq, "type": type_, "data": data, "prev": self.last_hash}
                event["hash"] = event_hash(self.last_hash, event)
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                                (event["seq"], type_, json.dumps(data, ensure_ascii=False), event["prev"], event["hash"]))
                self.events.append(event)
                out.append(event)
        return out

    def compact(self) -> dict:
        """Архивировать события до последнего StateSnapshot включительно.

        Снимок становится якорем цепочки: события до него уезжают в таблицу
        archive, журнал продолжает верифицироваться от хэша снимка.
        """
        snap_idx = None
        for i in range(len(self.events) - 1, -1, -1):
            if self.events[i]["type"] == "StateSnapshot":
                snap_idx = i
                break
        if snap_idx is None or snap_idx == 0:
            return {"archived": 0, "kept": len(self.events)}
        anchor = self.events[snap_idx]
        archived = self.events[:snap_idx + 1]
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS archive(seq INTEGER, type TEXT, data TEXT, prev TEXT, hash TEXT)")
            for ev in archived:
                self.db.execute("INSERT INTO archive VALUES (?,?,?,?,?)",
                                (ev["seq"], ev["type"], json.dumps(ev["data"], ensure_ascii=False), ev["prev"], ev["hash"]))
            self.db.execute("DELETE FROM events WHERE seq <= ?", (anchor["seq"],))
            self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                            (anchor["seq"], anchor["type"], json.dumps(anchor["data"], ensure_ascii=False), anchor["prev"], anchor["hash"]))
        self.events = self.events[snap_idx:]
        self.db.execute("VACUUM")
        self.db.commit()
        return {"archived": len(archived) - 1, "kept": len(self.events), "anchor_hash": anchor["hash"]}

    def verify_sampled(self, every: int = 10) -> tuple[bool, str]:
        """Ленивая проверка: края и каждое N-е событие вместо полной цепочки."""
        if not self.events:
            return True, "empty"
        idx = sorted(set([0, len(self.events) - 1] + list(range(0, len(self.events), every))))
        prev_map = {ev["seq"]: ev for ev in self.events}
        for i in idx:
            ev = self.events[i]
            body = {k: v for k, v in ev.items() if k != "hash"}
            # После compaction первый сохранённый StateSnapshot является якорем
            # прежней цепочки, поэтому его prev намеренно не равен GENESIS.
            if i == 0 and ev.get("type") == "StateSnapshot":
                prev_hash = ev.get("prev", GENESIS)
            else:
                prev_hash = GENESIS if i == 0 else self.events[i - 1]["hash"]
            if ev.get("prev") != prev_hash or event_hash(prev_hash, body) != ev.get("hash"):
                return False, f"event {i}: нарушение на выборке"
        return True, f"sampled ok ({len(idx)}/{len(self.events)})"

    def verify_chain(self) -> tuple[bool, str]:
        # Проверяем архив целиком, а не только активное окно после compaction.
        # Повторный compact может записать якорь в archive ещё раз, поэтому
        # одинаковые дубликаты по seq допустимы, а конфликтующие — нет.
        has_archive = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive'"
        ).fetchone() is not None
        archived: list[dict[str, Any]] = []
        if has_archive:
            by_seq: dict[int, dict[str, Any]] = {}
            rows = self.db.execute(
                "SELECT seq,type,data,prev,hash FROM archive ORDER BY seq"
            )
            for seq, typ, data, prev_hash, h in rows:
                ev = {"seq": seq, "type": typ, "data": json.loads(data), "prev": prev_hash, "hash": h}
                prior = by_seq.get(seq)
                if prior is not None and prior != ev:
                    return False, f"archive seq {seq}: conflicting duplicate"
                by_seq[seq] = ev
            archived = [by_seq[seq] for seq in sorted(by_seq)]
            prev = GENESIS
            for i, ev in enumerate(archived):
                body = {k: v for k, v in ev.items() if k != "hash"}
                if ev.get("prev") != prev or event_hash(prev, body) != ev.get("hash"):
                    return False, f"archive event {i}: chain corrupted"
                prev = ev["hash"]

        start = 0
        prev = GENESIS
        if archived and self.events:
            anchor = archived[-1]
            current = self.events[0]
            if current.get("seq") != anchor.get("seq") or current.get("hash") != anchor.get("hash"):
                return False, "active journal does not match archived anchor"
            prev = anchor["hash"]
            start = 1
        for i in range(start, len(self.events)):
            ev = self.events[i]
            body = {k: v for k, v in ev.items() if k != "hash"}
            if ev.get("prev") != prev or event_hash(prev, body) != ev.get("hash"):
                return False, f"event {i}: chain corrupted"
            prev = ev["hash"]
        return True, "ok"
