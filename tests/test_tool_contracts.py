import inspect

import pytest

from picklang.contracts import ContractViolation, ObjectContract, ScalarField, validated_tool


def test_types_are_exact_not_coerced():
    contract = ObjectContract({'count': ScalarField(int), 'ok': ScalarField(bool)})
    assert contract.validate({'count': 1, 'ok': True}) == {'count': 1, 'ok': True}
    for data in ({'count': True, 'ok': True}, {'count': 1, 'ok': 1}, {'count': '1', 'ok': True}):
        with pytest.raises(ContractViolation):
            contract.validate(data)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_numbers_rejected(value):
    with pytest.raises(ContractViolation):
        ObjectContract({'n': ScalarField(float)}).validate({'n': value})


@pytest.mark.parametrize('value', [None, [], {}, {'id': 'a', 'extra': 1}, {'id': ''}, {'id': '  '}, {'id': 1}])
def test_object_shape_and_required_fields(value):
    with pytest.raises(ContractViolation):
        ObjectContract({'id': ScalarField(str, nonempty=True)}).validate(value)


def test_contract_and_result_are_not_mutable_aliases():
    fields = {'id': ScalarField(str)}
    contract = ObjectContract(fields)
    fields.clear()
    result = {'id': 'a'}
    validated = contract.validate(result)
    result['id'] = 'b'
    assert validated == {'id': 'a'}
    with pytest.raises(TypeError):
        contract.fields['new'] = ScalarField(str)


def test_binding_checked_before_and_after_adapter():
    calls = []
    def observe(requested_id='a'):
        calls.append(requested_id)
        return {'id': 'wrong'}
    wrapped = validated_tool(observe, ObjectContract({'id': ScalarField(str, nonempty=True)}),
                             match_args={'id': 'requested_id'})
    assert inspect.signature(wrapped) == inspect.signature(observe)
    with pytest.raises(ContractViolation, match='argument'):
        wrapped(requested_id=1)
    assert calls == []
    with pytest.raises(ContractViolation, match='identifier mismatch'):
        wrapped()
    assert calls == ['a']


@pytest.mark.parametrize('fields', [{}, {'id': str}, {'': ScalarField(str)}])
def test_invalid_configuration_rejected(fields):
    with pytest.raises(ValueError):
        ObjectContract(fields)


def test_enums_and_invalid_bindings():
    rule = ScalarField(str, choices=('available', 'unknown'))
    with pytest.raises(ContractViolation):
        rule.validate('AVAILABLE', 'status')
    with pytest.raises(ValueError):
        ScalarField(bool, choices=(1,))
    with pytest.raises(ValueError):
        validated_tool(lambda x: {}, ObjectContract({'id': ScalarField(str)}), match_args={'id': 'missing'})
    async def async_adapter():
        return {}
    with pytest.raises(ValueError, match='async'):
        validated_tool(async_adapter, ObjectContract({'id': ScalarField(str)}))
