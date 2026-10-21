"""П.16 — CLI `pick`: run / check / fmt / bench / verify одной командой."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import PickSyntaxError


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def format_source(source: str) -> str:
    """Канонический форматтер: один смысл — один текст — один digest.

    Нормализует пробелы вокруг операторов и отступы; строки не трогает.
    """
    out = []
    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            out.append(line)
            continue
        for old, new in ((" =", " ="), ("= ", "= "), ("  ", " ")):
            pass
        import re
        line = re.sub(r"(?<![=!<>])\s*=\s*(?!=)", " = ", line)
        line = re.sub(r"\s*>>\s*", " >> ", line)
        line = re.sub(r"\s*->\s*", " -> ", line)
        line = re.sub(r"\s+\|\|\s+", " || ", line)
        line = re.sub(r" +", " ", line)
        out.append(line)
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pick")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check")
    s.add_argument("file")
    s.add_argument("--contracts", help="JSON-файл контрактов функций {имя: {args, returns, effect}}")
    s = sub.add_parser("fmt")
    s.add_argument("file")
    s = sub.add_parser("run")
    s.add_argument("file")
    s.add_argument("--inputs", default="{}")
    s = sub.add_parser("bench")
    s.add_argument("files", nargs="+")
    s = sub.add_parser("verify")
    s.add_argument("workdir")
    s.add_argument("execution")
    args = ap.parse_args(argv)

    try:
        return _dispatch(args)
    except PickSyntaxError as exc:
        # Документированный интерфейс «line N: что ожидалось» вместо traceback.
        print(f"error: line {exc.line}: {exc.hint} :: {exc.text.strip()}", file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    if args.cmd == "check":
        from .check import check_contracts, check_effects, check_source
        src = _read(args.file)
        contracts = json.loads(_read(args.contracts)) if args.contracts else None
        diags = check_source(src)
        if contracts is not None:
            diags += check_contracts(src, contracts)
        diags += check_effects(src, contracts)
        print(json.dumps(diags, ensure_ascii=False, indent=1) if diags else "OK: замечаний нет")
        return 1 if any(d.get("level") == "error" for d in diags) else 0
    if args.cmd == "fmt":
        print(format_source(_read(args.file)), end="")
        return 0
    if args.cmd == "run":
        from . import run_program
        from .stdlib import with_stdlib
        out, _env, trace = run_program(_read(args.file), with_stdlib({}), json.loads(args.inputs))
        print(json.dumps({"output": out, "trace": trace}, ensure_ascii=False))
        return 0
    if args.cmd == "bench":
        from .result_guard import estimate_tokens
        for f in args.files:
            print(f"{f}: {estimate_tokens(_read(f))} ток.")
        return 0
    if args.cmd == "verify":
        from durable_weave import DurableExecutor
        ex = DurableExecutor(args.workdir, {})
        print(json.dumps(ex.verify(args.execution), ensure_ascii=False, indent=1))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
