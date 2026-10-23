"""Строгие контракты плоских результатов адаптеров без неявного приведения типов.

Это небольшой контракт scalar-object, НЕ реализация JSON Schema. Проверка
возвращает факты и не выбирает бизнес-ветвь; её по-прежнему выбирает PICK.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import inspect
import math
from types import MappingProxyType
from typing import Callable, Mapping


class ContractViolation(ValueError):
    """Ответ адаптера или связанный аргумент не соответствует контракту."""


@dataclass(frozen=True)
class ScalarField:
    type: type
    choices: tuple | None = None
    nonempty: bool = False

    def __post_init__(self):
        if self.type not in (str, bool, int, float):
            raise ValueError("supported scalar types: str, bool, int, float")
        if self.nonempty and self.type is not str:
            raise ValueError("nonempty applies only to strings")
        if self.choices is not None:
            if type(self.choices) is not tuple or not self.choices:
                raise ValueError("choices must be a nonempty tuple")
            for value in self.choices:
                self.validate(value, "enum choice", check_choices=False)

    def validate(self, value, path: str, *, check_choices: bool = True):
        if type(value) is not self.type:
            raise ContractViolation(f"{path}: expected {self.type.__name__}")
        if self.type is float and not math.isfinite(value):
            raise ContractViolation(f"{path}: nonfinite number")
        if self.nonempty and not value.strip():
            raise ContractViolation(f"{path}: empty string")
        if check_choices and self.choices is not None and value not in self.choices:
            raise ContractViolation(f"{path}: invalid enum")


@dataclass(frozen=True)
class ObjectContract:
    fields: Mapping[str, ScalarField]

    def __post_init__(self):
        copied = dict(self.fields)
        if not copied or any(type(k) is not str or not k or not isinstance(v, ScalarField)
                             for k, v in copied.items()):
            raise ValueError("fields must map nonempty names to ScalarField")
        object.__setattr__(self, "fields", MappingProxyType(copied))

    def validate(self, result) -> dict:
        if type(result) is not dict:
            raise ContractViolation("result: expected plain dict")
        if set(result) != set(self.fields):
            # Не включаем сами недоверенные значения/ключи в сообщение об ошибке.
            raise ContractViolation("result: missing or unexpected fields")
        for name, rule in self.fields.items():
            rule.validate(result[name], f"result.{name}")
        return dict(result)


def validated_tool(fn: Callable, contract: ObjectContract, *,
                   match_args: Mapping[str, str] | None = None) -> Callable:
    """Проверить sync-адаптер, включая result.id == аргумент requested_id.

    Поля/аргументы привязки проверяются при настройке. Ошибка результата бросается
    до его возврата в PICK и до коммита durable-активности. Внешний вызов самого
    адаптера уже состоялся: обёртка не транзакция и не sandbox.
    """
    if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(getattr(fn, "__call__", None)):
        raise ValueError("async adapters are unsupported by validated_tool")
    signature = inspect.signature(fn)
    bindings = dict(match_args or {})
    for field, argument in bindings.items():
        if field not in contract.fields or argument not in signature.parameters:
            raise ValueError("unknown contract field or bound argument")

    @wraps(fn)
    def call(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        for field, argument in bindings.items():
            contract.fields[field].validate(bound.arguments[argument], f"argument.{argument}")
        result = contract.validate(fn(*args, **kwargs))
        for field, argument in bindings.items():
            expected = bound.arguments[argument]
            if type(result[field]) is not type(expected) or result[field] != expected:
                raise ContractViolation(f"result.{field}: identifier mismatch")
        return result

    return call
