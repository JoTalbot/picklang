"""П.2 (транспорт) — Передача контекста между ПРОЦЕССАМИ-агентами по digest.

Слоты живут в CAS конкретного агента. Чтобы другой процесс получил контекст,
не пересылают «всю историю»: публикуется bundle = манифест слотов (имена -> ref)
+ сами CAS-объекты; получатель проверяет каждый объект по хэшу до импорта.
Переходящая между агентами строка (handoff) — одна: digest манифеста + diff.

Формат бандла — обычный JSON-файл, поэтому транспорт может быть чем угодно:
файл, очередь, HTTP. Здесь — каталог inbox: publish кладёт, receive забирает.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cas import ContentAddressedStore, canonical_json, sha256_hex
from .slots import SlotStore


class ContextTransport:
    def __init__(self, inbox: str | Path):
        self.inbox = Path(inbox)
        self.inbox.mkdir(parents=True, exist_ok=True)

    def publish(self, store: SlotStore, names: list[str] | None = None,
                base: dict[str, Any] | None = None) -> dict[str, Any]:
        """Собрать bundle из слотов (всех или перечисленных) и положить в inbox.

        Возвращает handoff — компактное сообщение для другого агента:
        {handoff: digest, slots: [...], diff: {...}} — его и платит LLM токенами.
        """
        snapshot = store.slice(names) if names else store.snapshot()
        objects: dict[str, Any] = {}
        slots: dict[str, str] = {}
        for name, value in snapshot.items():
            ref = store.cas.put_json(value)
            slots[name] = ref
            objects[ref] = value
        manifest = {"slots": slots, "objects": objects}
        digest = sha256_hex(canonical_json(manifest))
        (self.inbox / f"{digest}.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        handoff: dict[str, Any] = {"handoff": digest, "slots": sorted(slots)}
        if base is not None:
            handoff["diff"] = SlotStore.diff(base, snapshot)
        return handoff

    def receive(self, handoff_digest: str, target: SlotStore) -> dict[str, Any]:
        """Забрать bundle по digest, проверить каждый объект по хэшу, импортировать."""
        path = self.inbox / f"{handoff_digest}.json"
        if not path.exists():
            raise KeyError(f"контекстный бандл не найден: {handoff_digest}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if sha256_hex(canonical_json(manifest)) != handoff_digest:
            raise ValueError("digest бандла не совпал: манифест подменён")
        for ref, value in manifest["objects"].items():
            actual = target.cas.put_json(value)
            if actual != ref:
                raise ValueError(f"объект {ref[:12]}… не сошёлся по хэшу при импорте")
        for name, ref in manifest["slots"].items():
            target.index[name] = ref
        target._save()
        return target.snapshot()
