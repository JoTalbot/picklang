"""Issue #63: deterministic, pure, fail-closed conditional branches."""
import pytest

from picklang.parser import parse, PickSyntaxError
from picklang.ir import compile_ir
from picklang.check import check_source, check_contracts, check_effects
from picklang.interpreter import run_program, PickRuntimeError
from picklang.aot import compile_program
from picklang.proof import prove_safety
from durable_weave.pickrun import run_pick


def test_exactly_one_branch_and_scope():
    source = '''> choose(status)
@if status == "available"
    local = strict()
    out local
@else_if status == "unknown"
    local = manual()
    out local
@else
    ? false -> fail "unsupported status"
@end'''
    for status, expected in [('available', 'strict'), ('unknown', 'manual')]:
        calls = []
        registry = {name: (lambda n=name: calls.append(n) or n) for name in ('strict', 'manual')}
        output, env, trace = run_program(source, registry, {'status': status})
        assert output == expected and calls == trace == [expected]
        assert 'local' not in env
    assert not check_source(source, {'strict': lambda: '', 'manual': lambda: ''})


@pytest.mark.parametrize('condition', ['null', '1', '"yes"', 'missing', 'data.missing', 'true == 1', 'false or data.missing', 'true and null'])
def test_bad_conditions_have_no_effect(condition):
    source = f'@if {condition}\neffect()\n@else\neffect()\n@end\nout 1'
    calls = []
    with pytest.raises(PickRuntimeError):
        run_program(source, {'effect': lambda: calls.append(1)}, {'data': {}})
    assert calls == []


def test_all_case_conditions_validated_before_effects():
    calls = []
    with pytest.raises(PickRuntimeError):
        run_program('@if true\neffect()\n@else_if missing\neffect()\n@end\nout 1',
                    {'effect': lambda: calls.append(1)})
    assert calls == []


@pytest.mark.parametrize('source', [
    '@if true\nout 1', '@else\nout 1', '@end',
    '@if true\nout 1\n@else\nout 2\n@else\nout 3\n@end',
    '@if true\n@else\nout 1\n@end',
    '@if true\nout 1\n@else\nout 2\n@else_if true\nout 3\n@end',
    '@if tool()\nout 1\n@end', '@if data >> tool()\nout 1\n@end',
    '@if true\nfor x in [1]: f(x)\n@end',
    '@if true\n@wait approval :1s else f()\n@end',
])
def test_invalid_syntax(source):
    with pytest.raises(PickSyntaxError):
        parse(source)


def test_depth_limit():
    with pytest.raises(PickSyntaxError, match='8'):
        parse('@if true\n' * 9 + 'out 1\n' + '@end\n' * 9)


def test_nested_boolean_and_ir():
    source = '''> choose(a, b)
@if (a == "available" and b == "none") or false
@if not false
out 42
@else
out 0
@end
@else
out -1
@end'''
    assert run_program(source, {}, {'a': 'available', 'b': 'none'})[0] == 42
    assert not check_source(source)
    assert compile_ir(source)['steps'][1]['cases'][0]['steps'][0]['kind'] == 'conditional'
    with pytest.raises(ValueError, match='conditional'):
        compile_program(source)
    assert prove_safety(source)['safe']


@pytest.mark.parametrize(('prefix', 'condition', 'code'), [
    ('', '1', 'CONDITION_TYPE'), ('', 'missing and true', 'UNDEFINED_VAR'),
    ('x = {ok: true}\n', 'x.missing', 'UNKNOWN_FIELD'),
    ('', 'true == 1', 'CONDITION_TYPE'),
])
def test_checker_condition_errors(prefix, condition, code):
    ds = check_source(prefix + f'@if {condition}\nout 1\n@else\nout 0\n@end')
    assert code in {d['code'] for d in ds}


def test_branch_scope_and_types():
    source = '@if true\nx = 1\n@else\nx = "bad"\n@end\nout x'
    assert {'BRANCH_TYPE', 'UNDEFINED_VAR'} <= {d['code'] for d in check_source(source)}
    source = 'x = 0\n@if true\nx = 1\n@end\nout x'
    assert 'BRANCH_SCOPE' in {d['code'] for d in check_source(source)}
    with pytest.raises(PickRuntimeError, match='overwrite'):
        run_program(source, {})


def test_contracts_and_effects_in_branches():
    source = 'x = lookup()\n@if x.missing\nbank.charge(1)\n@end\nout 0'
    assert any(d['code'] == 'UNKNOWN_FIELD' and d['level'] == 'error'
               for d in check_contracts(source, {'lookup': {'returns': ['ok']}}))
    assert check_effects(source)[0]['code'] == 'EFFECT_NO_COMPENSATION'


def test_no_match_is_failure():
    with pytest.raises(PickRuntimeError, match='no conditional branch'):
        run_program('@if false\nout 1\n@end', {})


def test_compensation_retains_branch_local_values():
    calls = []
    source = '@if true\nx = reserve() ! release(x)\n@end\n? false -> fail "stop"\nout 0'
    with pytest.raises(PickRuntimeError):
        run_program(source, {'reserve': lambda: 42, 'release': lambda x: calls.append(x)})
    assert calls == [42]


def test_durable_branch_resume(tmp_path):
    source = '> choose(status)\n@if status == "unknown"\nx = draft()\n@wait approval\nout x\n@else\nout strict()\n@end'
    calls = []
    registry = {'draft': lambda: calls.append('draft') or 'manual',
                'strict': lambda: pytest.fail('unselected branch executed')}
    result, ex = run_pick(source, registry, {'status': 'unknown'}, tmp_path, 'e')
    assert result.status == 'WAITING'
    ex.deliver_signal('e', 'approval', True)
    result, _ = run_pick(source, registry, {'status': 'unknown'}, tmp_path, 'e')
    assert result.status == 'COMPLETED' and result.output == 'manual'
    assert calls == ['draft']


@pytest.mark.parametrize(('status', 'freshness', 'conflict', 'expected'), [
    ('available', 'current', 'none', 'strict'),
    ('available', 'stale', 'none', 'review'),
    ('available', 'current', 'present', 'review'),
    ('unknown', 'current', 'none', 'review'),
    ('conflict', 'current', 'present', 'review'),
    ('error', 'current', 'none', None),
    ('invalid', 'current', 'none', None),
    ('available', 'invalid', 'none', None),
    ('available', 'current', 'invalid', None),
])
def test_availability_business_contract(status, freshness, conflict, expected):
    from pathlib import Path
    source = Path('examples/availability.pick').read_text()
    calls = []
    registry = {name + '.draft': (lambda id, n=name: calls.append(n) or n)
                for name in ('strict', 'review')}
    fact = {'id': 'item-1', 'status': status, 'freshness': freshness, 'conflict_status': conflict}
    inputs = {'fact': fact, 'requested_id': 'item-1'}
    assert not check_source(source, registry)
    if expected is None:
        with pytest.raises(PickRuntimeError):
            run_program(source, registry, inputs)
        assert calls == []
    else:
        assert run_program(source, registry, inputs)[0] == expected
        assert calls == [expected]
    calls.clear()
    inputs['requested_id'] = 'other'
    with pytest.raises(PickRuntimeError):
        run_program(source, registry, inputs)
    assert calls == []


def test_temporal_generated_code_rejects_feature_before_any_step():
    from durable_weave.temporal_adapter import generate_temporal_code
    import ast
    module = ast.parse(generate_temporal_code())
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef))
    run = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'run')
    assert isinstance(run.body[0], ast.If)
    assert 'conditional' in ast.unparse(run.body[0])


def test_temporal_runtime_rejects_before_host_effect():
    pytest.importorskip('temporalio')
    import asyncio
    from durable_weave.temporal_adapter import build_temporal_workflow
    from temporalio.exceptions import ApplicationError
    cls, _ = build_temporal_workflow()
    ir = compile_ir('effect()\n@if true\nout 1\n@end')
    with pytest.raises(ApplicationError, match='does not support conditional'):
        asyncio.run(cls().run(ir, {}))


def test_condition_never_invokes_python_comparison_hooks():
    calls = []
    class Dangerous:
        def __eq__(self, other):
            calls.append('eq')
            return True
    with pytest.raises(PickRuntimeError):
        run_program('> test(x)\n@if x == "yes"\nout 1\n@end', {}, {'x': Dangerous()})
    assert calls == []


def test_escaped_quotes_and_boolean_delimiters_in_string():
    source = '@if ("a\\\" and b" == "a\\\" and b")\nout 1\n@else\nout 0\n@end'
    assert run_program(source, {})[0] == 1
