"""П.16 — CLI `pick`: run / check / fmt / bench / verify одной командой."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .parser import PickSyntaxError


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _normalize_code_segment(segment: str) -> str:
    """Normalize PICK operators without touching quoted literal contents."""
    segment = re.sub(r"(?<![=!<>])\s*=\s*(?!=)", " = ", segment)
    segment = re.sub(r"\s*>>\s*", " >> ", segment)
    segment = re.sub(r"\s*->\s*", " -> ", segment)
    segment = re.sub(r"\s+\|\|\s+", " || ", segment)
    return re.sub(r" +", " ", segment)


def _format_line_preserving_strings(line: str) -> str:
    parts: list[str] = []
    code: list[str] = []
    quote: str | None = None
    i = 0

    def flush_code() -> None:
        if code:
            parts.append(_normalize_code_segment("".join(code)))
            code.clear()

    while i < len(line):
        if quote is not None:
            if line[i] == "\\" and i + 1 < len(line):
                parts.append(line[i:i + 2])
                i += 2
                continue
            if line.startswith(quote, i):
                parts.append(quote)
                i += len(quote)
                quote = None
                continue
            parts.append(line[i])
            i += 1
            continue

        ch = line[i]
        if ch in ("\"", "'"):
            flush_code()
            quote = ch * 3 if line.startswith(ch * 3, i) else ch
            parts.append(quote)
            i += len(quote)
            continue
        code.append(ch)
        i += 1

    flush_code()
    return "".join(parts)


def format_source(source: str) -> str:
    """Канонический форматтер: один смысл — один текст — один digest.

    Нормализует пробелы вокруг операторов; содержимое строковых литералов
    сохраняет, включая пробелы и операторы.
    """
    out = []
    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            out.append(line)
            continue
        out.append(_format_line_preserving_strings(line))
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
