"""П.9 — Политика эскалации к человеку: не «всегда», а по риск × неуверенность."""
from __future__ import annotations

from typing import Any


def decide(amount: float = 0, confidence: float = 1.0, irreversible: bool = False,
           amount_threshold: float = 1000, confidence_threshold: float = 0.7) -> dict[str, Any]:
    risk = (amount / max(1.0, amount_threshold)) + (1.0 - confidence) + (0.5 if irreversible else 0)
    if irreversible and amount >= amount_threshold:
        action, reason = "approve", "необратимо и дорого"
    elif confidence < confidence_threshold:
        action, reason = "approve", "низкая уверенность модели"
    elif risk >= 1.0:
        action, reason = "approve", "совокупный риск выше порога"
    else:
        action, reason = "auto", "дёшево, обратимо, модель уверена"
    return {"action": action, "risk": round(risk, 2), "reason": reason}


def human_summary(action_desc: str, decision: dict) -> str:
    """Одна строка человеку: что собираюсь сделать и почему спрашиваю."""
    return f"Собираюсь: {action_desc}. Спрашиваю, потому что: {decision['reason']} (риск {decision['risk']})."
