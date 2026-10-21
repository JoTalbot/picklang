# picklang 0.1.6

Patch release focused on reducing live prompt overhead while retaining strict correctness checks.

- Use task-scoped PICK instructions for the six paired benchmark tasks instead of repeating the full DSL grammar on every request.
- Keep operator-specific guidance and examples only where needed; unknown tasks fall back to the complete grammar.
- Add regression tests for prompt size, required operator guidance, and safe fallback behavior.
- Strengthen the release gate: the paired benchmark must pass every task with identical outputs, provide nonzero usage telemetry, and demonstrate lower total reported tokens for PICK than classic Python. Rate-limited or incomplete runs are inconclusive and cannot publish this optimization release.
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
