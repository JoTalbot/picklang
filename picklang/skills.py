"""П.2 — Навыки: успешные траектории становятся переиспользуемыми активами.

Проверенное исполнение (COMPLETED + verify) дистиллируется в навык:
программа + входы/выход-пример + digest. Лежит в CAS, ищется по цели.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from durable_weave.cas import ContentAddressedStore


class SkillLibrary:
    def __init__(self, workdir: str | Path):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.cas = ContentAddressedStore(self.workdir / "cas")
        self.index_path = self.workdir / "skills.json"
        self.index: dict[str, Any] = {}
        if self.index_path.exists():
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))

    def register(self, name: str, source: str, example_inputs: dict, example_output: Any,
                 verified: bool) -> str:
        if not verified:
            raise ValueError("навык регистрируется только из верифицированного исполнения")
        from .ir import program_digest
        skill = {"name": name, "source": source, "digest": program_digest(source),
                 "inputs": example_inputs, "output": example_output}
        ref = self.cas.put_json(skill)
        self.index[name] = ref
        self.index_path.write_text(json.dumps(self.index, indent=1), encoding="utf-8")
        return ref

    def get(self, name: str) -> dict[str, Any]:
        return self.cas.get_json(self.index[name])

    def find(self, goal: str) -> dict[str, Any] | None:
        for name in self.index:
            skill = self.get(name)
            if skill["source"].lstrip().startswith(f"> {goal}("):
                return skill
        return None
