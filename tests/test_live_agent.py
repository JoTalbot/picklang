import pytest

from picklang.live_agent import extract_json


def test_extract_json_from_markdown_fence():
    assert extract_json('```json\n{"goal": "x"}\n```') == {"goal": "x"}


def test_extract_json_rejects_garbage():
    with pytest.raises(Exception):
        extract_json("тут нет никакого json")
