"""П.17 — Eval-корпус: меряем не только токены, но и УСПЕШНОСТЬ.

20 типовых задач: программа на PICK + моки хоста + ожидаемый результат.
Прогон считает: дошло до результата / упало, токены программы, число вызовов.
Это та метрика, по которой сравнивают агентные системы, а не «короче текст».
"""
TASKS = []
def task(name, src, registry, inputs, expected):
    TASKS.append({"name": name, "src": src, "registry": registry, "inputs": inputs, "expected": expected})

task("charge_ok", "> t(u)\np = bank.charge(u, 5)\nout p.tx\n",
     {"bank.charge": lambda u, a: {"ok": True, "tx": "t1"}}, {"u": "x"}, "t1")
task("guard_block", "> t(u)\np = bank.charge(u, 5)\n? p.ok -> fail \"нет\"\nout p.tx\n",
     {"bank.charge": lambda u, a: {"ok": True, "tx": "t2"}}, {"u": "x"}, "t2")
task("pipeline_top", "> t(q)\nr = web.search(q) >> top(1)\nout r\n",
     {"web.search": lambda q: [1, 2, 3]}, {"q": "x"}, [1])
task("loop_sum", "> t(xs)\nfor i in xs: y = dbl(i)\nout y\n",
     {"dbl": lambda i: i * 2}, {"xs": [2, 3]}, 6)
task("parallel", "> t(q)\na = f(q) || b = g(q)\nout {a: a, b: b}\n",
     {"f": lambda q: 1, "g": lambda q: 2}, {"q": "x"}, {"a": 1, "b": 2})
task("wait_ok", "> t(x)\n@wait approval\n? approval -> fail \"нет\"\nout 1\n",
     {}, {"x": 1}, 1)
task("map_out", "> t(x)\nout {v: x, w: x}\n", {}, {"x": 9}, {"v": 9, "w": 9})
task("retry_flaky", "> t(x)\ny = flaky() ^3\nout y\n",
     {"flaky": lambda: 7}, {"x": 1}, 7)
task("budget_ok", "> t(x)\n!budget steps<3\ny = f()\nout y\n",
     {"f": lambda: 5}, {"x": 1}, 5)
task("cmp_done", "> t(x)\ny = f()\ndone when: y == 5\nout y\n",
     {"f": lambda: 5}, {"x": 1}, 5)
task("fields_proj", "> t(q)\nr = web.search(q) >> fields(\"a\")\nout r\n",
     {"web.search": lambda q: {"a": 1, "b": 2}}, {"q": "x"}, {"a": 1})
task("nested_get", "> t(x)\np = bank.charge(x, 1)\nout p.tx\n",
     {"bank.charge": lambda x, a: {"tx": "z9"}}, {"x": 1}, "z9")
task("two_steps", "> t(x)\na = f(x)\nb = g(a)\nout b\n",
     {"f": lambda x: x + 1, "g": lambda a: a * 10}, {"x": 1}, 20)
task("saga_comp_ok", "> t(x)\na = f(x) ! undo(a)\nout a\n",
     {"f": lambda x: 3, "undo": lambda a: None}, {"x": 1}, 3)
task("budget_tokens", "> t(x)\n!budget tokens<10000\ny = f()\nout y\n",
     {"f": lambda: "ok"}, {"x": 1}, "ok")
task("wait_else", "> t(x)\n@wait approval :1s else note.log(\"t\")\nout 2\n",
     {"note.log": lambda s: None}, {"x": 1}, 2)
task("effect_money", "> t(x)\np = bank.charge(x, 1) ! bank.refund(p.tx) @fx money\nout p.tx\n",
     {"bank.charge": lambda x, a: {"tx": "m1"}, "bank.refund": lambda t: None}, {"x": 1}, "m1")
task("count_pipe", "> t(q)\nn = web.search(q) >> count()\nout n\n",
     {"web.search": lambda q: [1, 2]}, {"q": "x"}, 2)
task("keys_pipe", "> t(x)\nk = d.get(x) >> keys()\nout k\n",
     {"d.get": lambda x: {"b": 1, "a": 2}}, {"x": 1}, ["a", "b"])
task("take_pipe", "> t(x)\ns = txt.get(x) >> take(3)\nout s\n",
     {"txt.get": lambda x: "abcdef"}, {"x": 1}, "abc…")
