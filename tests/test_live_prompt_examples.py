from bench_live_adapters import PICK_TASK_EXAMPLES
from eval_live_cascade import GRAMMAR, TASKS, make_validator


def task_named(name):
    return next(task for task in TASKS if task["name"] == name)


def test_prompt_contains_pipeline_and_sequential_dependency_examples():
    assert "results = web.search(q) >> top(1)" in PICK_TASK_EXAMPLES["search_top1"]
    assert "msg = mail.send(u, sub.id)" in PICK_TASK_EXAMPLES["multi_step_subscribe"]
    assert "out {sub: sub.id, message: msg.id}" in PICK_TASK_EXAMPLES["multi_step_subscribe"]
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
