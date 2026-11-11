"""Offline differential corpus for interpreter/AOT observable semantics.

The corpus deliberately compares outputs, final environments, host-call ordering,
and normalized failure surfaces. It does not call model APIs or external services.
"""
from types import SimpleNamespace

import pytest

from picklang import run_program
from picklang.stdlib import with_stdlib


def _run_both(source, registry_factory, inputs):
    observed = []
    for prefer_aot in (False, True):
        calls = []

        def record(label, value):
            calls.append((label, value))
            return value

        registry = registry_factory(calls, record)
        try:
            output = run_program(
                source,
                registry,
                dict(inputs),
                prefer_aot=prefer_aot,
            )
        except BaseException as exc:  # Compare language-visible failure surfaces too.
            observed.append(("error", type(exc).__name__, str(exc), calls))
        else:
            observed.append(("ok", output, calls))

    assert observed[0] == observed[1], (source, observed)
    return observed[0]


def test_nested_host_object_field_reads_match():
    def registry(calls, record):
        return {"load": lambda key: SimpleNamespace(meta=SimpleNamespace(id=key))}

    result = _run_both(
        "> t(key)\nitem = load(key)\nout item.meta.id\n",
        registry,
        {"key": "part-17"},
    )

    assert result[0] == "ok"
    assert result[1][0] == "part-17"


def test_recursive_top_fields_projection_matches():
    source = (
        '> t(rows)\n'
        'selected = rows >> top(2) >> fields("id")\n'
        'out selected\n'
    )
    rows = [
        [{"id": 1, "private": "a"}, {"id": 2, "private": "b"}],
        [{"id": 3, "private": "c"}],
        [{"id": 4, "private": "d"}],
    ]

    result = _run_both(
        source,
        lambda calls, record: with_stdlib({}),
        {"rows": rows},
    )

    assert result[0] == "ok"
    assert result[1][0] == [[{"id": 1}, {"id": 2}], [{"id": 3}]]


@pytest.mark.parametrize("values", [[1, 2, 3], []])
def test_loop_definedness_and_host_call_order_match(values):
    def registry(calls, record):
        def twice(value):
            calls.append(("twice", value))
            return value * 2

        return {"twice": twice}

    result = _run_both(
        "> t(xs)\nfor i in xs: y = twice(i)\nout y\n",
        registry,
        {"xs": values},
    )

    if values:
        assert result[0] == "ok"
        assert result[1][0] == values[-1] * 2
        assert result[2] == [("twice", value) for value in values]
    else:
        assert result[0] == "error"
        assert result[1] == "PickRuntimeError"
        assert "не определена" in result[2]
        assert result[3] == []


def test_sequential_side_effect_order_and_values_match():
    def registry(calls, record):
        return {"record": record}

    result = _run_both(
        '> t(x)\na = record("first", x)\n'
        'b = record("second", a)\nout b\n',
        registry,
        {"x": 9},
    )

    assert result[0] == "ok"
    assert result[1][0] == 9
    assert result[2] == [("first", 9), ("second", 9)]


def test_failure_after_side_effect_preserves_error_and_effect_prefix():
    def registry(calls, record):
        def explode(value):
            calls.append(("explode", value))
            raise ValueError("offline corpus failure")

        return {"record": record, "explode": explode}

    result = _run_both(
        "> t(x)\na = record(\"before-failure\", x)\n"
        "b = explode(a)\nout b\n",
        registry,
        {"x": 5},
    )

    assert result[0] == "error"
    assert result[1] == "PickRuntimeError"
    assert "offline corpus failure" in result[2]
    assert result[3] == [("before-failure", 5), ("explode", 5)]
