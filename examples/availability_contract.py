"""Локальный демонстрационный контракт; не схема недоступного razborka."""
from picklang.contracts import ObjectContract, ScalarField, validated_tool

AVAILABILITY_CONTRACT = ObjectContract({
    "id": ScalarField(str, nonempty=True),
    "status": ScalarField(str, choices=("available", "unknown", "conflict", "error")),
    "freshness": ScalarField(str, choices=("current", "stale")),
    "conflict_status": ScalarField(str, choices=("none", "present")),
})


def checked_observer(observe):
    """observe(requested_id) возвращает только проверенные факты, не решение."""
    return validated_tool(observe, AVAILABILITY_CONTRACT,
                          match_args={"id": "requested_id"})
