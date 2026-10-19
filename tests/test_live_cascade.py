import pytest

from eval_live_cascade import TASKS, is_provider_rate_limit, load_model_tiers, make_validator


def test_model_tier_configuration_accepts_explicit_valid_json():
    assert load_model_tiers('[["small"], ["large", "fallback"]]') == [
        ["small"], ["large", "fallback"]
    ]


@pytest.mark.parametrize("raw", [
    "not-json",
    "[]",
    "[[]]",
    '[["small"], []]',
    '[["small", 4]]',
    '{"tier": "small"}',
])
def test_model_tier_configuration_rejects_invalid_json(raw):
    with pytest.raises(ValueError):
        load_model_tiers(raw)


def test_provider_rate_limit_detection_is_narrow():
    assert is_provider_rate_limit("HTTP 429: too many requests")
    assert is_provider_rate_limit("quota exceeded for this account")
    assert not is_provider_rate_limit("invalid PICK syntax")
    assert not is_provider_rate_limit("connection timed out")


def test_live_validator_checks_multi_step_output_not_just_syntax():
    task = next(item for item in TASKS if item["name"] == "multi_step_subscribe")
    validator = make_validator(task)
    valid = (
        "> t(u)\n"
        "sub = db.create_sub(u)\n"
        "m = mail.send(u, sub.id)\n"
        "out {sub: sub.id, message: m.id}\n"
    )
    assert validator(valid)
    assert not validator(valid.replace("message: m.id", "message: sub.id"))
    assert not validator("not a PICK program")
