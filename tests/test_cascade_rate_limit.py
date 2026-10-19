import pytest

import picklang.cascade as cascade_module
from picklang.cascade import ModelCascade


def test_rate_limit_stops_cascade_without_trying_more_models(monkeypatch):
    calls = []

    def rate_limited_chat(key, model, messages, **kwargs):
        calls.append(model)
        raise RuntimeError("HTTP 429: Rate limit exceeded")

    monkeypatch.setattr(cascade_module, "chat", rate_limited_chat)
    cascade = ModelCascade([["free/model-a", "free/model-b"], ["free/model-c"]])

    with pytest.raises(RuntimeError, match="provider rate limit; stopping cascade early"):
        cascade.run("test-key", [{"role": "user", "content": "test"}], lambda _: True)

    assert calls == ["free/model-a"]
    assert len(cascade.log) == 1
    assert cascade.log[0]["ok"] is False
    assert "429" in cascade.log[0]["error"]


def test_non_rate_limit_error_continues_to_next_model(monkeypatch):
    calls = []

    def flaky_chat(key, model, messages, **kwargs):
        calls.append(model)
        if model == "free/model-a":
            raise RuntimeError("temporary connection reset")
        return "valid answer", {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(cascade_module, "chat", flaky_chat)
    cascade = ModelCascade([["free/model-a", "free/model-b"]])

    result = cascade.run("test-key", [{"role": "user", "content": "test"}], lambda text: text == "valid answer")

    assert result["model"] == "free/model-b"
    assert calls == ["free/model-a", "free/model-b"]
