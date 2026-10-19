# Живой каскад моделей (OpenRouter, free tier, $0)

Валидатор: парсинг + check + исполнение на моках до ожидаемого результата.

| Задача | OK | Tier | Модель | Попыток | Сек | Токены |
|---|---|---|---|---:|---:|---:|
| charge_tx | True | 0 | liquid/lfm-2.5-2.6b:free | 1 | 9.0 | 249+2114 |
| search_top1 | True | 0 | poolside/laguna-xs-2.1:free | 2 | 8.6 | 549+1538 |
| double_last | True | 1 | nvidia/nemotron-3-super-120b-a12b:free | 3 | 38.9 | 512+3351 |
| fields_a | True | 1 | nvidia/nemotron-3-super-120b-a12b:free | 3 | 33.9 | 803+2516 |
| count_results | True | 0 | liquid/lfm-2.5-2.6b:free | 1 | 14.5 | 241+3501 |
| guard_charge | True | 1 | nvidia/nemotron-3-super-120b-a12b:free | 3 | 422.9 | 723+5500 |

Итого: 6/6; дешёвым tier-0 решено 3; всего токенов 21597.
Оговорка: free tier нестабилен — цифры одного прогона, а не статистика.
Зафиксированная аномалия: guard_charge заняла 423 с при таймауте попытки 45 с.
Причина: urllib-таймаут — на каждую операцию чтения, а не на запрос целиком;
медленно капающий ответ его не пробивает. Для продакшна нужен общий дедлайн
на попытку, а не только per-read timeout. В этом же прогоне: один 429 у Laguna XS
(каскад просто пошёл дальше) и один read-timeout у неё же на guard_charge.
