## Не выпущено — надёжность и issue #63

- Ограниченные `@if / @else_if / @else / @end`: чистые boolean-условия,
  строгие типы, fail-closed, вложенность до 8 и локальные переменные ветки.
- Интерпретатор и локальный durable replay поддерживают выбранный путь;
  AOT/Temporal явно отклоняют feature. `prove_safety` не выдаёт доказательство
  для пока не поддерживаемого анализа ветвей.
- Добавлены пример availability без реальных интеграций и регрессионные тесты.
- Исправлены коллизии параллельных шагов sync/async, проверки входов replay,
  привязка исходника PICK к исполнению, сохранение сигналов/идентичности в снимках
  и восстановление метаданных старых снимков из SQLite-архива.
- Исправлены кеширование `None` и конкурентная публикация CAS-объектов.
- CI исправлен для `JoTalbot/picklang`, PR unit-проверки идут на Python
  3.11/3.12/3.13; Temporal-интеграция вынесена отдельно.
- Совместимость журналов и ограничения описаны в `docs/RELIABILITY.md`;
  синтаксис и область видимости — в `docs/LANGUAGE.md`.

# picklang 0.1.10

Patch release focused on stricter benchmark telemetry and report integrity.

- Require finite, non-negative duration telemetry for every paired PICK and classic Python benchmark row.
- Require repeat counts to be actual positive integers, rejecting booleans, floats, and numeric strings instead of coercing them.
- Validate each row's repeat metadata as an integer matching the planned repeat.
- Add regression tests for malformed duration and repeat metadata.
- Preserve the live Groq cascade and paired benchmark as CI release gates; this release makes no new performance claim beyond the recorded CI run.

---

# picklang 0.1.9

Patch release focused on stricter benchmark report validation.

- Require the report-level status to be exactly `COMPLETED` before a paired benchmark can pass the release gate.
- Reject malformed non-object JSON report values with a clear validation error.
- Add regression tests for missing status, unknown status, and non-object reports.

This patch strengthens release-gate integrity; it does not claim a new live benchmark measurement.

---

# picklang 0.1.8

Patch release focused on trustworthy paired live benchmarks and release-gate integrity.

- Validate that every planned task and repeat is present exactly once for both PICK and classic Python adapters.
- Reject task/repeat ordering mismatches, differing or missing input digests, contradictory model telemetry, incomplete/rate-limited reports, failed tasks, output mismatches, missing token telemetry, and runs without token savings.
- Add regression coverage for benchmark pairing and input-integrity failures.
- Keep the 12-task live Groq cascade as an independent correctness gate and print live benchmark results directly in Actions logs and job summary.

Verified on 2026-10-21 on the trusted self-hosted ARM64 runner: the live Groq cascade passed 12/12 tasks at tier-0 in 12 attempts, using 3,446 reported tokens and 5.4 seconds summed task duration. The paired same-model benchmark passed 6/6 tasks for each adapter with exact output matches; PICK used 1,724 reported tokens versus 2,105 for classic Python, saving 381 tokens (18.1%). This is one live sample on a small fixture, not a universal cost or latency guarantee.

---

# picklang 0.1.7

Patch release focused on lower live-generation overhead and fewer fallback calls.

- Use task-scoped prompts and executable examples for all 12 live-cascade tasks, not only the six paired straight-line tasks.
- Preserve a full-grammar fallback for unknown task names.
- Add a strict saga rollback instruction: compensation stays attached to the charge step, then the guard fails, preserving rollback order.
- Add regression tests that run all 12 examples through the real task validators, including guards, compensation, `@wait`, parallel branches, and budgets.

Verified on 2026-10-21 with the live Groq cascade: 12/12 tasks passed on tier-0 in 12 attempts, using 3,470 reported tokens and 4.6 seconds summed task duration. The previous comparable run used 6,525 tokens, 14 attempts, and 9.7 seconds. This is a single live sample, not a latency guarantee.

The paired same-model PICK-vs-classic-Python gate also passed: 6/6 tasks for each adapter, 6/6 exact output matches, 1,703 PICK tokens vs 2,102 Python tokens (19.0% fewer reported tokens). This measures the tested fixture, not universal end-to-end agent cost.

---

# picklang 0.1.6

Patch release focused on reducing live prompt overhead while retaining strict correctness checks.

- Use task-scoped PICK instructions for the six paired benchmark tasks instead of repeating the full DSL grammar on every request.
- Keep operator-specific guidance and a short executable example for each paired task; unknown tasks fall back to the complete grammar.
- Add regression tests for prompt size, required operator guidance, and safe fallback behavior.
- Strengthen the release gate: the paired benchmark must pass every task with identical outputs, provide nonzero usage telemetry, and demonstrate lower total reported tokens for PICK than classic Python. Rate-limited or incomplete runs are inconclusive and cannot publish this optimization release.
- Fix false HTTP 429 detection caused by matching the digits inside decimal timing telemetry; add a regression test for the exact boundary case.
- Keep the separate 12-task Groq cascade as an independent correctness gate.

Verified v0.1.5 baseline on 2026-10-21: same-model paired run passed 6/6 tasks for both PICK and classic Python, with identical outputs on all six. PICK used 2,710 reported tokens and 4.94 seconds summed invocation time; Python used 2,105 tokens and 3.30 seconds. This is a single small sample, not a general performance claim. v0.1.6 will publish only if the new live run demonstrates a token reduction without task regressions.

---

# picklang 0.1.2

Patch release.

- Add an executable paired A/B benchmark runner that invokes PICK and baseline adapters on identical task inputs.
- Alternate adapter execution order, enforce per-invocation timeouts, and preserve raw outputs, timing, model/token telemetry, and input digests.
- Document the adapter protocol and explicitly distinguish adapter-reported success, semantic equivalence, token usage, and billed cost.
- Keep the release gate: main-branch unit/package checks and all six live Groq cascade tasks must pass before publication.

The paired runner is a harness, not evidence that PICK is faster or cheaper by itself. Such claims require actual paired runs with equivalent validators and comparable provider/model settings. Live evaluation consumes provider quota.

---

# picklang 0.1.1

Patch release.

- Preserve the CAS reference when a strict result budget forces the preview to be dropped, so callers can still recover the complete result.
- Add live-cascade configuration and multi-step validator regression tests.
- Run pull-request tests and package builds on GitHub-hosted runners; retain the main-only self-hosted and live-provider checks.
- Correct benchmark documentation to distinguish source-token counts from end-to-end agent cost.

Release gate: the main-branch workflow publishes this version only after unit/package checks and the six-task Groq live cascade both pass. The live evaluation consumes provider quota.

---

# picklang 0.1.0

Первый упакованный релиз: язык PICK для намерений LLM-агентов и durable-исполнитель DurableWeave.

- Язык: одна строка = один шаг, саги с компенсациями, @wait, стражи, бюджеты, for, ||, пайплайны проекций, словарь блоков.
- Исполнение: интерпретатор + AOT (локальные переменные, batch-циклы, саги), авто-выбор пути, async-вариант, снимки состояния и компакция журнала.
- Надёжность: CAS, хэш-цепочка событий, replay без повторных сайд-эффектов, verify; нагрузка 100k событий — docs/LOAD.md.
- Модели: каскад (sequential и hedged), пакетная генерация, лимиты reasoning, общий дедлайн запроса.
- Интеграции: MCP (клиент, пул, PICK как сервер), Temporal (воркеры в отдельных процессах, рестарт посередине workflow).
- Установка: pip install -e . ; демо: python3 demo.py ; CLI: pick check/run/bench/verify.
