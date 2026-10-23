"""Сжатие обязано сохранять семантику всех структур, а не только roundtrip."""
import pytest

from picklang.interpreter import run_program
from picklang.minify import compress, expand


@pytest.mark.parametrize('source,inputs', [
    ('> choose(long_condition)\n@if long_condition\nlong_result = tool()\nout {long_result: long_result}\n@else\nout "none"\n@end', {'long_condition': True}),
    ('> choose(long_condition)\n@if long_condition and true\n@if not false\nlong_result = tool()\nout long_result\n@end\n@else\nout "none"\n@end', {'long_condition': True}),
    ('> run(long_items)\nfor long_item in long_items: long_result = echo(long_item)\nout long_result', {'long_items': [1, 2]}),
    ('> run(long_argument)\nlong_result = echo(long_argument) || other_result = echo(long_argument)\nout {long_result: long_result, other_result: other_result}', {'long_argument': 2}),
    ('> run(echo)\nlong_result = echo(echo)\nout {echo: long_result}', {'echo': 'argument'}),
    ('> run(tool)\nlong_result = tool.call(tool)\nout long_result', {'tool': 42}),
    ('long_value = "long_value \\" a"\n# long_value comment\nout long_value', {}),
    ('long_value = echo(1)\n@wait approval\nout {approval: approval, long_value: long_value}', {}),
])
def test_minify_preserves_execution(source, inputs):
    registry = {'tool': lambda: 42, 'echo': lambda x: x, 'tool.call': lambda x: x}
    mini, mapping = compress(source)
    assert expand(mini, mapping) == source
    mini_inputs = {mapping.get(k, k): v for k, v in inputs.items()}
    kwargs = {'signal_provider': lambda _: True, 'prefer_aot': False}
    before = run_program(source, registry, inputs, **kwargs)
    after = run_program(mini, registry, mini_inputs, **kwargs)
    assert after[0] == before[0]
    assert after[2] == before[2]
    assert 'approval' not in mapping


def test_conditional_local_names_are_compressed_without_capture():
    source = '> run(a)\n@if a\nlong_value = echo(a)\nout long_value\n@else\nout 0\n@end'
    mini, mapping = compress(source)
    assert 'long_value' in mapping
    assert len(set(mapping.values())) == len(mapping)
    assert not set(mapping.values()) & {'a', 'run', 'echo', 'out', 'if', 'else', 'end'}
    assert expand(mini, mapping) == source


def test_signal_name_is_a_public_interface_not_a_local_variable():
    source = '@if true\n@wait external_response\nout external_response\n@end'
    mini, mapping = compress(source)
    assert 'external_response' not in mapping
    received = []
    def provider(name):
        received.append(name)
        return 42 if name == 'external_response' else None
    assert run_program(mini, {}, signal_provider=provider)[0] == 42
    assert received == ['external_response']


def test_literal_data_does_not_create_fake_ir_variables():
    source = 'out [{"var": "not_a_variable"}]'
    mini, mapping = compress(source)
    assert mini == source and mapping == {}
