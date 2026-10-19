"""П.15 — Идемпотентные обработчики для денег «из коробки».

Не «обработчик обязан быть идемпотентным» в документации, а готовый враппер:
результат первого вызова по ключу (execution + idempotency_key из payload)
сохраняется в SQLite; повторный вызов возвращает его, не тревожа банк.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable


class IdempotentStore:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS idem(key TEXT PRIMARY KEY, result TEXT)")
        self.db.commit()

    def get(self, key: str):
        row = self.db.execute("SELECT result FROM idem WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, result: Any):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO idem VALUES (?,?)", (key, json.dumps(result, ensure_ascii=False)))


def idempotent(fn: Callable, store: IdempotentStore, name: str = "") -> Callable:
    """Обернуть обработчик активности: payload._idempotency_key решает всё."""
    def call(payload: dict):
        key_raw = payload.get("_idempotency_key")
        if key_raw is None:
            return fn(payload)  # без ключа дедуплицировать нечего — честно выполняем
        key = f"{name or fn.__name__}:{payload.get('_execution_id')}:{key_raw}"
        hit = store.get(key)
        if hit is not None:
            return hit
        result = fn(payload)
        store.put(key, result)
        return result
    return call
