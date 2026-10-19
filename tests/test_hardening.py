"""Закалка: property-паритет AOT<->интерпретатор на случайных программах,
фаззинг парсера (никаких исключений кроме PickSyntaxError), устойчивость IR."""
import random
import string

import pytest

from picklang import run_program
from picklang.ir import program_digest
from picklang.parser import PickSyntaxError, parse
from picklang.stdlib import with_stdlib

REG = with_stdlib({
    "f": lambda x: x + 3,
    "g": lambda x: x * 2,
    "h": lambda x: {"v": x, "w": x + 1},
    "lst": lambda x: [x, x + 1, x + 2],
    "dbl": lambda i: i * 2,
})


def random_program(rng: random.Random) -> tuple[str, dict]:
    x = rng.randint(0, 20)
    lines = ["> t(x)"]
    body = rng.choice([
        "y = f(x)\nz = g(y)\nout z",
        "y = h(x)\nout y.v",
        "y = lst(x) >> top(2)\nout y",
        "y = h(x) >> fields(\"v\")\nout y",
        "a = f(x) || b = g(x)\nout {a: a, b: b}",
        "for i in xs: y = dbl(i)\nout y",
        "y = f(x)\nout {r: y, s: x}",
    ])
    lines += body.split("\n")
    return "\n".join(lines) + "\n", {"x": x, "xs": [1, 2, 3]}


def test_property_aot_interpreter_parity_300_programs():
    rng = random.Random(20261019)
    for _ in range(300):
        src, inputs = random_program(rng)
        out_fast = run_program(src, REG, dict(inputs))
        out_slow = run_program(src, REG, dict(inputs), prefer_aot=False)
        assert out_fast[0] == out_slow[0], src
        assert program_digest(src) == program_digest(src)


def test_fuzz_parser_never_crashes_with_foreign_exceptions():
    rng = random.Random(7)
    alphabet = string.printable + "привет>>||!@?^:.,(){}[]=\"' \n"
    valid = "> t(x)\ny = f(x)\nout y\n"
    for i in range(600):
        if i % 2:
            s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        else:  # мутации валидной программы: страшнее случайного шума
            chars = list(valid)
            for _ in range(rng.randint(1, 8)):
                pos = rng.randrange(len(chars))
                chars[pos] = rng.choice(alphabet)
            s = "".join(chars)
        try:
            parse(s)
        except PickSyntaxError:
            pass  # единственный легальный исход для мусора
