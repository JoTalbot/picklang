import pytest
from picklang.proof import prove_safety


@pytest.mark.parametrize('source', [
    '@if true\n@wait approval\n? approval -> fail "no"\nbank.charge(1)\n@else\nout 0\n@end',
    '@wait approval\n? approval == true -> fail "no"\n@if true\nmail.send(1)\n@else\nbank.charge(1)\n@end',
    '@if true\n@if false\nout 1\n@else\nout 2\n@end\n@else\nout 3\n@end',
])
def test_paths_with_checked_approval_are_safe(source):
    assert prove_safety(source)['safe']


@pytest.mark.parametrize('source', [
    '@wait approval\nbank.charge(1)',
    '@if true\n@wait approval\n? approval -> fail "no"\n@else\nout 0\n@end\nbank.charge(1)',
    '@if true\n@wait approval\n? approval -> fail "no"\nbank.charge(1)\n@else\nbank.charge(1)\n@end',
    'for item in [1]: bank.charge(item)',
    'a = bank.charge(1) || b = harmless()',
    'out bank.charge(1)',
    '@wait approval :1s else harmless()\n? approval -> fail "no"\nbank.charge(1)',
    '@wait approval\n? approval -> fail "no"\napproval = false\nbank.charge(1)',
    '@if true\nout {value: bank.charge(1)}\n@end',
])
def test_unsafe_or_unproven_path_is_reported(source):
    result = prove_safety(source)
    assert not result['safe'] and result['violations']


def test_numeric_one_does_not_qualify_as_boolean_guard():
    assert not prove_safety('@wait approval\n? approval == 1 -> fail "no"\nbank.charge(1)')['safe']


def test_loop_cannot_carry_overwritten_approval():
    source = '@wait approval\n? approval -> fail "no"\nfor approval in [False]: harmless()\nbank.charge(1)'
    assert not prove_safety(source)['safe']
