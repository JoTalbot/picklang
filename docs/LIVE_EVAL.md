# Живой eval моделей на задачах намерения (live_eval_models.py)

| Модель | Задача | Итог | Токенов | Секунд |
|---|---|---|---:|---:|
| poolside/laguna-s-2.1:free | pay | OK | 284 | 3.0 |
| poolside/laguna-s-2.1:free | reserve | OK | 252 | 5.0 |
| poolside/laguna-s-2.1:free | refund | FAIL LLMError | ? | 0.3 |
| cohere/north-mini-code:free | pay | OK | 666 | 47.0 |
| cohere/north-mini-code:free | reserve | OK | 497 | 3.5 |
| cohere/north-mini-code:free | refund | FAIL GenesisError | 789 | 8.2 |

**Валидных намерений с первой попытки: 4/6.** Прогон на бесплатном тарифе: недоступность модели — тоже результат, она записана как FAIL.

Примечание: повторные прогоны дают 4–5 из 6 — бесплатные модели то отдают 429, то зависают; нестабильность провайдера измерена и является частью результата.
