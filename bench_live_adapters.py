#!/usr/bin/env python3
"""Live paired benchmark adapter for PICK and a classic Python-code baseline.
Input: one JSON line {"task": "...", "input": {...}}. Diagnostics belong on stderr.
"""
import ast, json, os, sys, time
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_live_cascade import GRAMMAR, TASKS, clean, load_model_tiers, make_validator
from picklang.cascade import ModelCascade
from picklang.live_agent import load_key

PYTHON_GRAMMAR = """Write exactly one Python function named solve, no markdown.
Use only the listed host functions; no imports, loops, conditionals, comprehensions, or filesystem/network/process APIs.
Use only straight-line assignments and a return. Function parameters must exactly match the listed input names.
Return the exact task result. You may use list indexing or slicing, dictionaries, and fields on returned objects.
Host functions: bank.charge(u, amount), web.search(q), dbl(i), d.get(x),
db.create_sub(u), mail.send(u, sub_id), count(items).
Examples:
def solve(q):
    results = web.search(q)
    return results[:1]

def solve(u):
    sub = db.create_sub(u)
    message = mail.send(u, sub["id"])
    return {"sub": sub["id"], "message": message["id"]}
"""
ALLOWED_AST = {ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
    ast.Assign, ast.Expr, ast.Call, ast.Name, ast.Load, ast.Store, ast.Constant,
    ast.Dict, ast.List, ast.Tuple, ast.Subscript, ast.Slice, ast.BinOp, ast.Mult, ast.Add,
    ast.Attribute, ast.UnaryOp, ast.USub, ast.UAdd}

def host_namespace(task):
    root = {}
    for dotted, function in task["registry"].items():
        parts = dotted.split(".")
        parent = root
        for part in parts[:-1]:
            parent = parent.setdefault(part, {})
        parent[parts[-1]] = function
    def wrap(value):
        if isinstance(value, dict):
            return SimpleNamespace(**{k: wrap(v) for k, v in value.items()})
        return value
    root["count"] = lambda items: len(items)
    return {key: wrap(value) for key, value in root.items()}

def execute_classic(source, task):
    source = source.strip()
    fence = chr(96) * 3
    if source.startswith(fence):
        source = source.split("\n", 1)[1] if "\n" in source else ""
        source = source.rsplit(fence, 1)[0].strip()
    tree = ast.parse(source, mode="exec")
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) or tree.body[0].name != "solve":
        raise ValueError("expected exactly one function named solve")
    fn = tree.body[0]
    expected_args = list(task["inputs"])
    if [arg.arg for arg in fn.args.args] != expected_args or fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs:
        raise ValueError("solve parameters must exactly match task input names")
    host = host_namespace(task)
    local_names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    raise ValueError("assignment targets must be simple local names")
                if target.id in set(expected_args) | set(host) | {"solve"}:
                    raise ValueError("cannot overwrite an input, host function, or solve")
                local_names.add(target.id)
    allowed_names = set(expected_args) | set(host) | local_names | {"solve"}
    allowed_attributes = set(task["tools"])
    for node in ast.walk(tree):
        if type(node) not in ALLOWED_AST:
            raise ValueError("disallowed Python syntax: " + type(node).__name__)
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise ValueError("unknown name: " + node.id)
        if isinstance(node, ast.Attribute):
            chain, cursor = [], node
            while isinstance(cursor, ast.Attribute):
                chain.append(cursor.attr)
                cursor = cursor.value
            if not isinstance(cursor, ast.Name):
                raise ValueError("only direct host-function attributes are allowed")
            chain.append(cursor.id)
            dotted = ".".join(reversed(chain))
            if dotted not in allowed_attributes:
                # Permit ordinary fields on local values returned by host calls, but
                # never private/dunder attributes or chained introspection.
                if not (
                    isinstance(cursor, ast.Name)
                    and cursor.id in local_names
                    and len(chain) == 2
                    and not chain[0].startswith("_")
                ):
                    raise ValueError("attribute is not a declared host function or safe local field: " + dotted)
    namespace = {"__builtins__": {}}
    namespace.update(host)
    exec(compile(tree, "<classic-adapter>", "exec"), namespace, namespace)
    return namespace["solve"](**task["inputs"])

def main():
    mode = os.environ.get("PICK_AB_MODE", "")
    if mode not in {"pick", "classic"}:
        print("PICK_AB_MODE must be pick or classic", file=sys.stderr)
        return 2
    try:
        request = json.loads(sys.stdin.readline())
        task_name, supplied = request["task"], request["input"]
        task = next(item for item in TASKS if item["name"] == task_name)
        if not isinstance(supplied, dict) or supplied.get("inputs") != task["inputs"]:
            raise ValueError("fixture inputs do not match canonical task")
        key = load_key()
        description = supplied.get("description", task["task"])
        started = time.monotonic()
        captured = {"output": None}
        if mode == "pick":
            prompt = GRAMMAR + f"\nФункции хоста: {', '.join(task['tools'])}\nВходные данные: {json.dumps(task['inputs'], ensure_ascii=False)}\nЗадача: {description}\n"
            validator = make_validator(task)
        else:
            prompt = (PYTHON_GRAMMAR + f"\nTask: {description}\nInputs: {json.dumps(task['inputs'], ensure_ascii=False)}\n"
                + f"Allowed tools: {', '.join(task['tools'])}\n")
            def validator(source):
                try:
                    output = execute_classic(source, task)
                    if output == task["expected"]:
                        captured["output"] = output
                        return True
                    return False
                except Exception:
                    return False
        cascade = ModelCascade(load_model_tiers(), per_model_timeout=40,
            reasoning_max_tokens=256, max_completion_tokens=768)
        result = cascade.run(key, [{"role": "user", "content": prompt}], validator)
        if mode == "pick":
            from picklang.check import check_source
            from picklang.interpreter import run_program
            from picklang.stdlib import with_stdlib
            source = clean(result["text"])
            registry = with_stdlib(task["registry"])
            errors = [item for item in check_source(source, registry) if item.get("level") == "error"]
            if errors:
                raise ValueError("validated PICK source no longer passes check")
            output, _, _ = run_program(source, registry, dict(task["inputs"]), prefer_aot=False)
            captured["output"] = output
        elapsed = round(time.monotonic() - started, 4)
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        for entry in cascade.log:
            for field in usage:
                usage[field] += (entry.get("usage") or {}).get(field, 0) or 0
        print(json.dumps({"ok": captured["output"] == task["expected"], "output": captured["output"],
            "model": result["model"], "attempts": len(cascade.log),
            "prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"],
            "seconds": elapsed}, ensure_ascii=False))
        return 0 if captured["output"] == task["expected"] else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}))
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
