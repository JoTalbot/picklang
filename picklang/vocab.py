"""П.1 — Словарь проекта (vocabulary) поверх CAS.

Проект один раз дорого определяет типовой блок шагов, блок кладётся в CAS
по digest, а дальше LLM пишет одну строку `&alias(a, b)` вместо всего блока.
Одинаковые тела блоков хранятся в CAS один раз, даже под разными алиасами.

Синтаксис вызова блока:   &pay(id, amt)
Тело блока — обычный PICK-фрагмент с плейсхолдерами {param}.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from durable_weave.cas import ContentAddressedStore


class Vocabulary:
    def __init__(self, workdir: str | Path):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.cas = ContentAddressedStore(self.workdir / "cas")
        self.index_path = self.workdir / "vocab.json"
        self.index: dict[str, Any] = {}
        if self.index_path.exists():
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.index_path.write_text(json.dumps(self.index, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, alias: str, params: list[str], body: str) -> str:
        """Зарегистрировать блок. Возвращает CAS-ref тела (digest)."""
        ref = self.cas.put_text(body)
        self.index[alias] = {"params": params, "ref": ref}
        self._save()
        return ref

    def get(self, alias: str) -> dict[str, Any]:
        if alias not in self.index:
            raise KeyError(f"блок не найден в словаре проекта: &{alias}")
        item = self.index[alias]
        return {"params": item["params"], "body": self.cas.get_bytes(item["ref"]).decode("utf-8"), "ref": item["ref"]}

    def verify(self, alias: str) -> bool:
        return self.cas.verify(self.index[alias]["ref"])

    # --- развёртка ------------------------------------------------------
    def expand(self, source: str) -> str:
        out_lines: list[str] = []
        for raw in source.splitlines():
            m = re.fullmatch(r"\s*&([A-Za-z_]\w*)\((.*)\)\s*", raw)
            if not m:
                out_lines.append(raw)
                continue
            block = self.get(m.group(1))
            args = [a.strip() for a in m.group(2).split(",")] if m.group(2).strip() else []
            if len(args) != len(block["params"]):
                raise ValueError(f"&{m.group(1)} ждёт {len(block['params'])} аргументов, дано {len(args)}")
            body = block["body"]
            for param, arg in zip(block["params"], args):
                body = body.replace("{" + param + "}", arg)
            out_lines.extend(body.splitlines())
        result = "\n".join(out_lines) + "\n"
        from .parser import parse
        parse(result)  # развёрнутая программа обязана парситься
        return result

    def refs(self) -> dict[str, str]:
        return {alias: item["ref"] for alias, item in self.index.items()}
