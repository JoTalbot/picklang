"""Живой LLM-цикл через OpenRouter: модель пишет намерение для Genesis,
ошибки чинятся патчами, исполнение — на durable-рантайме.

Ключ берётся из окружения OPENROUTER_API_KEY или из файла (--key-file).
В репозиторий ключ не попадает никогда: только в заголовок запроса.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

API = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODELS = [
    "cohere/north-mini-code:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/free",
]


class LLMError(Exception):
    pass


def load_key(key_file: str | None = None) -> str:
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"].strip()
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()
    raise LLMError("нет ключа: задайте OPENROUTER_API_KEY или --key-file")


def chat(key: str, model: str, messages: list[dict], timeout: int = 180,
         json_schema: dict | None = None, max_completion_tokens: int | None = None,
         reasoning_max_tokens: int | None = None) -> tuple[str, dict]:
    payload: dict = {"model": model, "messages": messages, "temperature": 0}
    if max_completion_tokens is not None:
        payload["max_tokens"] = max_completion_tokens
    if reasoning_max_tokens is not None:
        # кап скрытого рассуждения: на простых намерениях это главный налог
        payload["reasoning"] = {"max_tokens": reasoning_max_tokens}
    if json_schema is not None:
        # constrained decoding: провайдер не даст вернуть JSON вне схемы
        payload["response_format"] = {"type": "json_schema", "json_schema": json_schema}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/JoTalbot/testing", "X-Title": "pick-live-cycle"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as e:
        raise LLMError(f"{model}: {type(e).__name__}: {str(e)[:300]}") from e
    try:
        text = data["choices"][0]["message"]["content"]
    except Exception:
        raise LLMError(f"{model}: неожиданный ответ: {json.dumps(data)[:300]}")
    return text or "", data.get("usage", {})


def extract_json(text: str) -> Any:
    """Вытащить JSON из ответа модели, даже если она обернула его в markdown."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMError(f"модель не вернула JSON: {text[:200]!r}")
    return json.loads(text[start:end + 1])
