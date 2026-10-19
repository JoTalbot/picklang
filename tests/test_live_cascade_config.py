import json

import pytest

from eval_live_cascade import is_provider_rate_limit, load_model_tiers


def test_default_model_tiers_are_free():
    tiers = load_model_tiers("")
    assert len(tiers) == 2
    assert tiers[0][0].endswith(":free")


def test_model_tiers_can_be_configured():
    tiers = load_model_tiers('[["vendor/cheap-model"], ["vendor/fallback-model"]]')
    assert tiers == [["vendor/cheap-model"], ["vendor/fallback-model"]]


@pytest.mark.parametrize("raw", ["not-json", "{}", "[]", '[[]]', '[[" "]]'])
def test_invalid_model_tiers_are_rejected(raw):
    with pytest.raises(ValueError):
        load_model_tiers(raw)


@pytest.mark.parametrize("message", [
    "HTTP 429: Too Many Requests",
    "provider rate limit exceeded",
    "quota exceeded for free models",
])
def test_provider_quota_errors_trip_circuit_breaker(message):
    assert is_provider_rate_limit(message)


def test_non_quota_errors_do_not_trip_circuit_breaker():
    assert not is_provider_rate_limit("invalid response from model")
