# Eval-корпус агентных задач (автогенерация: eval_harness.py --write)

| Задача | Итог | Токенов программы | Вызовов |
|---|---|---:|---:|
| charge_ok | PASS | 20 | 1 |
| guard_block | PASS | 30 | 1 |
| pipeline_top | PASS | 20 | 2 |
| loop_sum | PASS | 20 | 2 |
| parallel | PASS | 28 | 2 |
| wait_ok | PASS | 23 | 0 |
| map_out | PASS | 17 | 0 |
| retry_flaky | PASS | 16 | 1 |
| budget_ok | PASS | 20 | 1 |
| cmp_done | PASS | 21 | 1 |
| fields_proj | PASS | 21 | 2 |
| nested_get | PASS | 20 | 1 |
| two_steps | PASS | 19 | 2 |
| saga_comp_ok | PASS | 18 | 1 |
| budget_tokens | PASS | 21 | 1 |
| wait_else | PASS | 24 | 0 |
| effect_money | PASS | 30 | 1 |
| count_pipe | PASS | 18 | 2 |
| keys_pipe | PASS | 18 | 2 |
| take_pipe | PASS | 20 | 2 |

**Успешность на моках: 20/20.** Это нижняя планка качества: программы написаны человеком; живой вариант — те же задачи через LLM (live_cycle_openrouter.py).
