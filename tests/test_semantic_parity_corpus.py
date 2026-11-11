"""Offline differential corpus for interpreter/AOT observable semantics.

The corpus deliberately compares outputs, final environments, host-call ordering,
and normalized failure surfaces. It does not call model APIs or external services.
"""
from types import SimpleNamespace

import pytest

from picklang import run_program
from picklang.stdlib import with_stdlib


def _run_both(source, registry_factory, inputs, *, compare_calls=True):
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

    if compare_calls:
        assert observed[0] == observed[1], (source, observed)
    elif observed[0][0] == observed[1][0] == "ok":
        # The third run_program tuple item is an internal dispatch trace;
        # stdlib built-ins are lowered differently by AOT, not host side effects.
        assert observed[0][1][:2] == observed[1][1][:2], (source, observed)
    else:
        assert observed[0][1:3] == observed[1][1:3], (source, observed)
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
        compare_calls=False,  # stdlib built-ins are not external host effects in AOT.
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


@pytest.mark.parametrize(
    ("source", "inputs", "registry_factory", "expected"),
    [
        (
            '> t()\nout {"status": "ok", "items": [1, 2, 3]}\n',
            {},
            lambda calls, record: {},
            {"status": "ok", "items": [1, 2, 3]},
        ),
        (
            '> t(payload)\nout payload.meta.id\n',
            {"payload": {"meta": {"id": "nested-9"}}},
            lambda calls, record: {},
            "nested-9",
        ),
        (
            '> t(values)\nselected = top(values, 2)\nout selected\n',
            {"values": [4, 5, 6]},
            lambda calls, record: {"top": lambda values, n: list(values)[:n]},
            [4, 5],
        ),
        (
            '> t(x)\na = first(x)\nb = second(a)\nout b\n',
            {"x": 7},
            lambda calls, record: {
                "first": lambda x: calls.append(("first", x)) or x + 1,
                "second": lambda x: calls.append(("second", x)) or x * 3,
            },
            24,
        ),
        (
            '> t(xs)\nfor item in xs: emit(item)\nout "done"\n',
            {"xs": ["a", "b", "c"]},
            lambda calls, record: {"emit": lambda item: calls.append(item)},
            "done",
        ),
        (
            '> t(x)\nvalue = lookup(x)\nout value.meta.code\n',
            {"x": "sku-2"},
            lambda calls, record: {
                "lookup": lambda x: SimpleNamespace(meta=SimpleNamespace(code=x.upper()))
            },
            "SKU-2",
        ),
        (
            '> t(x)\n? x > 10 -> fail "too large"\nout x\n',
            {"x": 4},
            lambda calls, record: {},
            4,
        ),
        (
            '> t(x)\n? x > 10 -> fail "too large"\nout x\n',
            {"x": 12},
            lambda calls, record: {},
            None,
        ),
        (
            '> t(xs)\nfor item in xs: y = wrap(item)\nout y\n',
            {"xs": [2, 4]},
            lambda calls, record: {"wrap": lambda item: {"value": item * 10}},
            {"value": 40},
        ),
        (
            '> t(x)\ny = absent(x)\nout y\n',
            {"x": 1},
            lambda calls, record: {},
            None,
        ),
    ],
)
def test_representative_scalar_collection_and_failure_cases_match(
    source, inputs, registry_factory, expected
):
    result = _run_both(source, registry_factory, inputs)
    if expected is None:
        assert result[0] == "error"
        assert result[1] == "PickRuntimeError"
    else:
        assert result[0] == "ok"
        assert result[1][0] == expected


def test_host_exception_surface_matches_without_losing_call_prefix():
    def registry(calls, record):
        def fail(value):
            calls.append(("fail", value))
            raise LookupError("offline lookup unavailable")

        return {"record": record, "fail": fail}

    result = _run_both(
        '> t(x)\na = record("started", x)\nb = fail(a)\nout b\n',
        registry,
        {"x": "part-8"},
    )
    assert result[0] == "error"
    assert result[1] == "PickRuntimeError"
    assert "offline lookup unavailable" in result[2]
    assert result[3] == [("started", "part-8"), ("fail", "part-8")]


def test_nested_collection_projection_keeps_shape_and_order():
    rows = [
        [{"id": "a", "secret": 1}, {"id": "b", "secret": 2}],
        [{"id": "c", "secret": 3}],
    ]
    result = _run_both(
        '> t(rows)\nselected = rows >> top(2) >> fields("id")\nout selected\n',
        lambda calls, record: with_stdlib({}),
        {"rows": rows},
        compare_calls=False,
    )
    assert result[0] == "ok"
    assert result[1][0] == [[{"id": "a"}, {"id": "b"}], [{"id": "c"}]]


@pytest.mark.parametrize(
    ("source", "inputs", "registry_factory", "expected"),
    [
        (
            '> t()\nout "готово"\n',
            {},
            lambda calls, record: {},
            "готово",
        ),
        (
            '> t(record)\nout record.id\n',
            {"record": {"id": "R-17", "private": True}},
            lambda calls, record: {},
            "R-17",
        ),
        (
            '> t(xs)\nresult = take(xs, 1)\nout result\n',
            {"xs": ["first", "second"]},
            lambda calls, record: {"take": lambda xs, n: list(xs)[:n]},
            ["first"],
        ),
        (
            '> t(x)\na = normalize(x)\nb = decorate(a)\nout b\n',
            {"x": "  part  "},
            lambda calls, record: {
                "normalize": lambda x: x.strip(),
                "decorate": lambda x: {"label": x.upper()},
            },
            {"label": "PART"},
        ),
    ],
)
def test_additional_parity_edges_match(source, inputs, registry_factory, expected):
    result = _run_both(source, registry_factory, inputs)
    assert result[0] == "ok"
    assert result[1][0] == expected
