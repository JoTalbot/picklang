"""Diff-режим — главная экономия токенов при итеративной разработке с LLM.

Классический цикл: LLM пересылает программу целиком на каждое изменение.
PICK-цикл: базовый артефакт уже лежит в CAS по digest; LLM присылает только
патч из построчных операций (~десятки токенов вместо сотен):

  @base <digest>            — от какой программы пляшем (проверяется по CAS/IR)
  ~12: pay = bank.charge(id, amt) ! bank.refund(pay.tx)   — заменить строку 12
  +15: ? pay.ok -> fail "банк отклонил"                    — вставить после 15
  -9                        — удалить строку 9

Применение патча без совпадения digest базы запрещено: нельзя «дообъяснять»
изменения к программе, которую модель не видела.
"""
from __future__ import annotations

import re


class PickPatchError(Exception):
    pass


def apply_patch(base_source: str, patch: str, expected_digest: str | None = None) -> str:
    from .ir import program_digest
    if expected_digest is not None and program_digest(base_source) != expected_digest:
        raise PickPatchError("digest базы не совпал: патч адресован другой программе")
    lines = base_source.splitlines()
    # Операции применяем снизу вверх, чтобы номера строк не «плыли».
    ops = []
    for raw in patch.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("@base"):
            continue
        m = re.fullmatch(r"~(\d+):\s*(.*)", line)
        if m: ops.append((int(m.group(1)), "replace", m.group(2))); continue
        m = re.fullmatch(r"\+(\d+):\s*(.*)", line)
        if m: ops.append((int(m.group(1)), "insert", m.group(2))); continue
        m = re.fullmatch(r"-(\d+)", line)
        if m: ops.append((int(m.group(1)), "delete", "")); continue
        raise PickPatchError(f"не понимаю операцию патча: {raw!r} (ожидается ~N:, +N: или -N)")
    for num, kind, text in sorted(ops, key=lambda o: -o[0]):
        if kind == "replace":
            if not (1 <= num <= len(lines)): raise PickPatchError(f"нет строки {num}")
            lines[num - 1] = text
        elif kind == "delete":
            if not (1 <= num <= len(lines)): raise PickPatchError(f"нет строки {num}")
            del lines[num - 1]
        elif kind == "insert":
            if not (0 <= num <= len(lines)): raise PickPatchError(f"нельзя вставить после строки {num}")
            lines.insert(num, text)
    result = "\n".join(lines) + "\n"
    from .parser import parse
    parse(result)  # патч, ломающий синтаксис, не применяется
    return result
