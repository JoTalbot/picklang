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

    @staticmethod
    def alias_cost(alias: str) -> int:
        """Сколько токенов платит модель за имя блока при каждом вызове."""
        from .result_guard import estimate_tokens
        return estimate_tokens(f"&{alias}()")

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
    _counter: int = 0

    @staticmethod
    def _rename_locals(body: str, alias: str, occurrence: int) -> str:
        """Гигиена блоков: переменные, присвоенные внутри блока, получают
        уникальный суффикс, чтобы два вызова &pay не делили одну `p`."""
        import re as _re
        locals_: list[str] = []
        for line in body.splitlines():
            m = _re.match(r"\s*([A-Za-z_]\w*)\s*=", line)
            if m and m.group(1) not in locals_:
                locals_.append(m.group(1))
        if not locals_:
            return body
        # Последняя присвоенная переменная блока — его «экспорт» (на неё
        # ссылается вызывающая программа, напр. p.tx у блока pay): не трогаем.
        # Переименовываем только внутренние временные — они и давали коллизии.
        export = locals_[-1] if locals_ else None
        mapping = {name: f"{name}__{alias}{occurrence}" for name in locals_ if name != export}
        def repl(m):
            name, prefix = m.group(0), ""
            return mapping.get(name, name)
        out = []
        for line in body.splitlines():
            # переименовываем только корни идентификаторов (не поля после точки, не строки)
            parts, quote, cur = [], None, ""
            i = 0
            tokens = _re.split(r'("[^"]*")', line)
            done = []
            for tok in tokens:
                if tok.startswith('"'):
                    done.append(tok); continue
                done.append(_re.sub(r"(?<![\w.])[A-Za-z_]\w*", lambda m: mapping.get(m.group(0), m.group(0)), tok))
            out.append("".join(done))
        return "\n".join(out)

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
            Vocabulary._counter += 1
            body = self._rename_locals(body, m.group(1), Vocabulary._counter)
            out_lines.extend(body.splitlines())
        result = "\n".join(out_lines) + "\n"
        from .parser import parse
        parse(result)  # развёрнутая программа обязана парситься
        return result

    def refs(self) -> dict[str, str]:
        return {alias: item["ref"] for alias, item in self.index.items()}
