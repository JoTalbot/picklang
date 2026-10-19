"""П.7 — Каскад моделей: сначала маленькая и быстрая, большая — только при провале проверки."""
from __future__ import annotations

from typing import Any, Callable

from .live_agent import chat


class ModelCascade:
    def __init__(self, tiers: list[list[str]], per_model_timeout: int = 45,
                 reasoning_max_tokens: int | None = 512, max_completion_tokens: int | None = 1024):
        self.tiers = tiers  # [[дешёвые...], [сильные...]]
        self.timeout = per_model_timeout
        # живой замер: модели жгли 1.5–5.5k токенов рассуждений на ответ из
        # трёх строк — капы по умолчанию, а не опция, о которой надо вспомнить
        self.reasoning_max_tokens = reasoning_max_tokens
        self.max_completion_tokens = max_completion_tokens
        self.log: list[dict[str, Any]] = []

    def run(self, key: str, messages: list[dict], validator: Callable[[str], bool],
            json_schema: dict | None = None) -> dict[str, Any]:
        for tier_no, models in enumerate(self.tiers):
            for model in models:
                try:
                    text, usage = chat(key, model, messages, timeout=self.timeout, json_schema=json_schema,
                                       max_completion_tokens=self.max_completion_tokens,
                                       reasoning_max_tokens=self.reasoning_max_tokens)
                    ok = bool(text.strip()) and validator(text)
                except Exception as e:
                    message = str(e)
                    self.log.append({"tier": tier_no, "model": model, "ok": False, "error": message[:120]})
                    # HTTP 429 usually means provider quota/rate limiting. Trying every
                    # remaining free model/task immediately only burns requests without
                    # producing useful evaluation data.
                    if "HTTP 429" in message or "Rate limit exceeded" in message:
                        raise RuntimeError(f"provider rate limit; stopping cascade early: {message[:180]}") from e
                    continue
                self.log.append({"tier": tier_no, "model": model, "ok": ok, "usage": usage})
                if ok:
                    return {"model": model, "tier": tier_no, "text": text, "usage": usage, "log": self.log}
        raise RuntimeError(f"каскад исчерпан: {self.log}")


class HedgedCascade(ModelCascade):
    """Hedged-вариант: сильная модель стартует параллельно с задержкой.

    Последовательный каскад платит суммой задержек tier-0 и tier-1.
    Здесь tier-1 запускается через hedge_delay секунд, если tier-0 ещё
    не дал валидного ответа; побеждает первый валидный, остальные потоки —
    daemon, их результаты игнорируются (HTTP уже не отменить, но общий
    дедлайн попытки режет ожидание).
    """

    def run_hedged(self, key: str, messages: list[dict], validator: Callable[[str], bool],
                   hedge_delay: float = 5.0, json_schema: dict | None = None) -> dict[str, Any]:
        import queue
        import threading

        results: "queue.Queue[dict]" = queue.Queue()
        done = threading.Event()

        def tier_runner(tier_no: int, models: list[str]):
            for model in models:
                if done.is_set():
                    return
                try:
                    text, usage = chat(key, model, messages, timeout=self.timeout, json_schema=json_schema,
                                       max_completion_tokens=self.max_completion_tokens,
                                       reasoning_max_tokens=self.reasoning_max_tokens)
                    ok = bool(text.strip()) and validator(text)
                except Exception as e:
                    self.log.append({"tier": tier_no, "model": model, "ok": False, "error": str(e)[:120]})
                    continue
                self.log.append({"tier": tier_no, "model": model, "ok": ok, "usage": usage})
                if ok:
                    results.put({"model": model, "tier": tier_no, "text": text, "usage": usage, "log": self.log})
                    done.set()
                    return

        threads = []
        t0 = threading.Thread(target=tier_runner, args=(0, self.tiers[0]), daemon=True)
        t0.start(); threads.append(t0)
        hedged = threading.Timer(hedge_delay, lambda: (
            threads.append(threading.Thread(target=tier_runner, args=(1, self.tiers[1]), daemon=True)),
            threads[-1].start()))
        if len(self.tiers) > 1:
            hedged.daemon = True
            hedged.start()
        try:
            # ждём победителя не дольше суммы разумных ожиданий
            return results.get(timeout=self.timeout + hedge_delay + 5)
        except Exception as e:
            raise RuntimeError(f"hedged-каскад исчерпан: {self.log}") from e
        finally:
            done.set()
            hedged.cancel()
