from picklang import run_program
from picklang.cli import format_source


def test_formatter_preserves_string_literal_contents_and_semantics():
    literals = [
        ('"a  b = c >> d -> e || f"', "a  b = c >> d -> e || f"),
        ("'it\\'s  x = y'", "it's  x = y"),
        ('"""triple  = >> -> ||"""', "triple  = >> -> ||"),
    ]

    for literal, expected in literals:
        source = f"> task()\nvalue={literal}\nout value\n"
        formatted = format_source(source)

        assert formatted == f"> task()\nvalue = {literal}\nout value\n"
        assert run_program(source, {}, {})[0] == expected
        assert run_program(formatted, {}, {})[0] == expected
        assert format_source(formatted) == formatted


def test_formatter_preserves_pipeline_and_parallel_markers_inside_strings():
    literal = '"a  b >> c || d"'
    source = f"> task()\nvalue={literal}\nout value\n"

    assert format_source(source) == f"> task()\nvalue = {literal}\nout value\n"


def test_formatter_still_normalizes_operators_outside_strings():
    source = "> task()\nvalue=make(x)>>top(2)->done\nout value\n"
    formatted = format_source(source)

    assert formatted == "> task()\nvalue = make(x) >> top(2) -> done\nout value\n"
