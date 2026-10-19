"""Живой LLM-цикл через OpenAI-совместимый API: модель пишет намерение для Genesis,
ошибки чинятся патчами, исполнение — на durable-рантайме.

Ключ берётся из GROQ_API_KEY, OPENROUTER_API_KEY или из файла (--key-file).
В репозиторий ключ не попадает никогда: только в заголовок запроса.
"""
from __future__ import annotations

import http.client
import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

API = os.environ.get("PICK_LIVE_API_URL", "https://openrouter.ai/api/v1/chat/completions")
DEFAULT_MODELS = [
    "cohere/north-mini-code:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/free",
]


class LLMError(Exception):
    pass


def _post_with_deadline(url: str, body: bytes, headers: dict, deadline_seconds: float) -> dict:
    """POST с ОБЩИМ дедлайном на весь запрос.

    Раньше стоял urllib timeout: он считается на каждое чтение сокета,
    и медленно капающий ответ (живой случай: 423 с при "таймауте 45 с")
    его никогда не пробивал. Теперь перед каждым чтением сокету выставляется
    оставшееся время, а по истечении дедлайна — жёсткая ошибка.
    """
    parsed = urllib.parse.urlparse(url)
    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    deadline = time.monotonic() + deadline_seconds
    conn = conn_cls(parsed.hostname, parsed.port, timeout=deadline_seconds)
    try:
        conn.request("POST", parsed.path or "/", body=body, headers=headers)
        resp = conn.getresponse()
        chunks = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"общий дедлайн {deadline_seconds} с исчерпан")
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
            # read1, не read: буферизованный read ждал бы буфер целиком,
            # обновляя сокет-таймаут на каждый капнувший кусок, — дедлайн бы не сработал
            chunk = resp.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        if resp.status >= 400:
            raise http.client.HTTPException(f"HTTP {resp.status}: {raw[:200]!r}")
        return json.loads(raw)
    finally:
        conn.close()


def load_key(key_file: str | None = None) -> str:
    for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY"):
        if os.environ.get(name):
            return os.environ[name].strip()
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()
    raise LLMError("нет ключа: задайте GROQ_API_KEY, OPENROUTER_API_KEY или --key-file")


def chat(key: str, model: str, messages: list[dict], timeout: int = 180,
         json_schema: dict | None = None, max_completion_tokens: int | None = None,
         reasoning_max_tokens: int | None = None) -> tuple[str, dict]:
    payload: dict = {"model": model, "messages": messages, "temperature": 0}
    if max_completion_tokens is not None:
        payload["max_tokens"] = max_completion_tokens
    if reasoning_max_tokens is not None and "api.groq.com" not in API:
        # OpenRouter supports this cap; Groq's OpenAI-compatible endpoint does not.
        payload["reasoning"] = {"max_tokens": reasoning_max_tokens}
    if json_schema is not None:
        # constrained decoding: провайдер не даст вернуть JSON вне схемы
        payload["response_format"] = {"type": "json_schema", "json_schema": json_schema}
    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/JoTalbot/testing", "X-Title": "pick-live-cycle"}
    try:
        data = _post_with_deadline(API, body, headers, timeout)
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
