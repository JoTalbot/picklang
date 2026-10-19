# Живой A/B: sequential против hedged каскада (OpenRouter, free, $0)

| Режим | Задача | OK | Сек | Попыток | Токенов | Модель |
|---|---|---|---:|---:|---:|---|
| sequential | charge_tx | True | 17.2 | 2 | 1949 | poolside/laguna-xs-2.1:free |
| sequential | double_last | True | 13.9 | 3 | 2067 | nvidia/nemotron-3-super-120b-a12b:free |
| sequential | guard_charge | True | 18.5 | 3 | 2278 | nvidia/nemotron-3-super-120b-a12b:free |
| hedged | charge_tx | True | 12.3 | 3 | 1699 | nvidia/nemotron-3-super-120b-a12b:free |
| hedged | double_last | True | 6.7 | 2 | 1625 | poolside/laguna-xs-2.1:free |
| hedged | guard_charge | True | 22.7 | 3 | 2232 | nvidia/nemotron-3-super-120b-a12b:free |

Итого sequential: 3/3, 50 с, 6294 токенов.

Итого hedged: 3/3, 42 с, 5556 токенов.

Оговорка: один срез на шумном free tier. Масштаб шума виден прямо здесь:
sequential guard_charge в этом прогоне — 18.5 с, а в предыдущем живом прогоне
та же задача заняла 423 с. Итог среза: hedged 42 с против 50 с суммарно
и не больше токенов (5556 против 6294) — выигрыш есть, но на таком шуме
это тенденция, а не доказанная константа; hedged страхует от хвоста задержек,
а не ускоряет медиану гарантированно.
