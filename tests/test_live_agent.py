import pytest

from picklang.live_agent import extract_json


def test_extract_json_from_markdown_fence():
    assert extract_json('```json\n{"goal": "x"}\n```') == {"goal": "x"}


def test_extract_json_rejects_garbage():
    with pytest.raises(Exception):
        extract_json("тут нет никакого json")


def test_load_key_prefers_groq_key(monkeypatch):
    from picklang.live_agent import load_key

    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    assert load_key() == "groq-test-key"


def test_load_key_supports_openrouter_fallback(monkeypatch):
    from picklang.live_agent import load_key

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    assert load_key() == "openrouter-test-key"
