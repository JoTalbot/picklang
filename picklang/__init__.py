"""PICK — компактный язык, рассчитанный на автора-LLM, а не на автора-человека.

Идея: LLM платит токенами за каждый символ boilerplate, за каждое повторение
уже известного контекста и за каждую неоднозначность, которую приходится
«дообъяснять». PICK убирает все три статьи расхода (см. docs/LANGUAGE.md).
"""
from .parser import parse, PickSyntaxError
from .interpreter import run_program, Registry
from .ir import compile_ir, program_digest

__all__ = ["parse", "PickSyntaxError", "run_program", "Registry", "compile_ir", "program_digest"]
__version__ = "0.1.0"
