"""П.3 (третьей волны) — Семантический кэш подзадач поверх CAS.

Одинаковое поднамерение — одинаковый результат, без повторного вызова модели
или инструмента. Ключ кэша: namespace (версия словаря/промптов) + имя функции
+ канонический digest аргументов. Результат лежит в CAS по digest.

Честные границы:
- кэшируются только функции, явно помеченные cacheable (обычно llm.* и чтения);
- функции с сайд-эффектами (bank.charge, mail.send) в кэш не пускаются by design;
- смена namespace инвалидирует всё разом: поменяли промпт — старые ответы мертвы;
- TTL опционален; digest аргументов — это точное, а не «примерное» совпадение.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from durable_weave.cas import ContentAddressedStore, canonical_json, sha256_hex


class SemanticCache:
    def __init__(self, workdir: str | Path, namespace: str = "v1", ttl_seconds: float | None = None):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.cas = ContentAddressedStore(self.workdir / "cas")
        self.namespace = namespace
        self.ttl = ttl_seconds
        self.index_path = self.workdir / "semcache.json"
        self.index: dict[str, Any] = {}
        if self.index_path.exists():
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.stats = {"hits": 0, "misses": 0}

    def _save(self):
        self.index_path.write_text(json.dumps(self.index, indent=1), encoding="utf-8")

    def key(self, name: str, args: list) -> str:
        return sha256_hex(canonical_json({"ns": self.namespace, "fn": name, "args": args}))

    def get(self, name: str, args: list) -> Any | None:
        entry = self.index.get(self.key(name, args))
        if not entry:
            self.stats["misses"] += 1
            return None
        if self.ttl is not None and time.time() - entry["ts"] > self.ttl:
            self.stats["misses"] += 1
            return None
        if not self.cas.verify(entry["ref"]):
            self.stats["misses"] += 1
            return None
        self.stats["hits"] += 1
        return self.cas.get_json(entry["ref"])

    def put(self, name: str, args: list, result: Any) -> str:
        ref = self.cas.put_json(result)
        self.index[self.key(name, args)] = {"ref": ref, "ts": time.time(), "fn": name}
        self._save()
        return ref

    def wrap_registry(self, registry: dict[str, Callable], cacheable: set[str]) -> dict[str, Callable]:
        """Обернуть cacheable-функции реестра; остальные проходят как есть."""
        wrapped = dict(registry)
        for name in cacheable:
            if name not in registry:
                continue
            fn = registry[name]

            def make(name=name, fn=fn):
                def call(*args):
                    hit = self.get(name, list(args))
                    if hit is not None:
                        return hit
                    result = fn(*args)
                    self.put(name, list(args), result)
                    return result
                return call

            wrapped[name] = make()
        return wrapped

    def cached_run(self, program_source: str, inputs: dict, runner: Callable[[], Any]) -> tuple[Any, bool]:
        """Кэш целого запуска: digest программы + входы -> результат.

        Второй агент с той же программой и входами получает результат из CAS,
        не выполняя ни одного вызова. runner вызывается только при промахе.
        """
        from .ir import program_digest
        key_args = {"program": program_digest(program_source), "inputs": inputs}
        hit = self.get("__run__", [key_args])
        if hit is not None:
            return hit, True
        result = runner()
        self.put("__run__", [key_args], result)
        return result, False
