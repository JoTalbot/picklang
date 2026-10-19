"""П.2 — Контекст как адресное пространство: слоты состояния поверх CAS.

Вместо пересказа всей истории агент/субагент получает слоты по именам и diff
между снимками: что появилось и что изменилось — токены только за изменения.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cas import ContentAddressedStore


class SlotStore:
    def __init__(self, workdir: str | Path):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.cas = ContentAddressedStore(self.workdir / "cas")
        self.index_path = self.workdir / "slots.json"
        self.index: dict[str, str] = {}
        if self.index_path.exists():
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save(self):
        self.index_path.write_text(json.dumps(self.index, indent=1), encoding="utf-8")

    def put(self, name: str, value: Any, max_tokens: int | None = None) -> str:
        """При превышении бюджета слот хранит summary + ref на полное значение:
        полное не теряется (оно в CAS), но в контекст ходит только выжимка."""
        if max_tokens is not None:
            from picklang.result_guard import estimate_tokens
            if estimate_tokens(value) > max_tokens:
                full_ref = self.cas.put_json(value)
                text = str(value)
                summary = {"summary": text[:400] + "…", "ref": full_ref,
                           "tokens_full": estimate_tokens(value)}
                ref = self.cas.put_json(summary)
                self.index[name] = ref
                self._save()
                return ref
        ref = self.cas.put_json(value)
        self.index[name] = ref
        self._save()
        return ref

    def get(self, name: str) -> Any:
        return self.cas.get_json(self.index[name])

    def snapshot(self) -> dict[str, Any]:
        return {name: self.get(name) for name in self.index}

    def slice(self, names: list[str]) -> dict[str, Any]:
        """Срез контекста для субагента: только перечисленные слоты."""
        return {name: self.get(name) for name in names if name in self.index}

    @staticmethod
    def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for key, value in after.items():
            if key not in before:
                out[key] = {"added": value}
            elif before[key] != value:
                out[key] = {"changed": value}
        return out
