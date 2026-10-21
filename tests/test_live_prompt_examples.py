from bench_live_adapters import PICK_TASK_EXAMPLES, build_pick_prompt
from eval_live_cascade import GRAMMAR, TASKS, make_validator


def task_named(name):
    return next(task for task in TASKS if task["name"] == name)


def test_prompt_contains_pipeline_and_sequential_dependency_examples():
    assert "results = web.search(q) >> top(1)" in PICK_TASK_EXAMPLES["search_top1"]
    assert "msg = mail.send(u, sub.id)" in PICK_TASK_EXAMPLES["multi_step_subscribe"]
    assert "out {sub: sub.id, message: msg.id}" in PICK_TASK_EXAMPLES["multi_step_subscribe"]
    assert set(PICK_TASK_EXAMPLES) == {task["name"] for task in TASKS[:6]}
    assert len(GRAMMAR) > 0


def test_pipeline_example_executes_to_expected_output():
    task = task_named("search_top1")
    source = "> search(q)\nresults = web.search(q) >> top(1)\nout results"
    assert make_validator(task)(source)


def test_sequential_dependency_example_executes_to_expected_output():
    task = task_named("multi_step_subscribe")
    source = (
        "> notify(u)\n"
        "sub = db.create_sub(u)\n"
        "msg = mail.send(u, sub.id)\n"
        "out {sub: sub.id, message: msg.id}"
    )
    assert make_validator(task)(source)



def test_task_scoped_prompts_are_shorter_than_full_grammar_for_paired_tasks():
    for task in TASKS[:6]:
        compact = build_pick_prompt(task)
        full = (
            GRAMMAR
            + f"\\nФункции хоста: {', '.join(task['tools'])}"
            + f"\\nВходные данные: {__import__('json').dumps(task['inputs'], ensure_ascii=False)}"
            + f"\\nЗадача: {task['task']}\\n"
        )
        assert task["task"] in compact
        assert len(compact) < len(full), task["name"]


def test_task_scoped_prompts_include_required_operator_guidance():
    by_name = {task["name"]: task for task in TASKS}
    assert "top(1)" in build_pick_prompt(by_name["search_top1"])
    assert "fields" in build_pick_prompt(by_name["fields_a"])
    assert "count()" in build_pick_prompt(by_name["count_results"])
    assert "sub.id" in build_pick_prompt(by_name["multi_step_subscribe"])
    assert "results = web.search(q) >> top(1)" in build_pick_prompt(by_name["search_top1"])


def test_unknown_task_falls_back_to_full_grammar():
    task = {"name": "unknown", "task": "unknown task", "tools": ["fn"], "inputs": {"x": 1}}
    prompt = build_pick_prompt(task)
    assert GRAMMAR in prompt
    assert "unknown task" in prompt


def test_all_six_compact_examples_pass_the_real_task_validators():
    for task in TASKS[:6]:
        assert make_validator(task)(PICK_TASK_EXAMPLES[task["name"]]), task["name"]
