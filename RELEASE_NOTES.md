# picklang 0.1.4

Patch release focused on parity and release validation.

- Fix Temporal generated workflow code so comparison expressions are evaluated, including explicit rejection of unsupported comparison operators.
- Add a regression test that compiles generated Temporal source and verifies comparison support before the optional SDK integration check.
- Make the live-task grammar explicit again, include a short canonical example, and print bounded rejected-response diagnostics directly in Actions logs to catch prompt regressions.
- Keep the release gate tied to the exact main commit: unit tests, package build and smoke checks, and all six live Groq cascade tasks must pass.
- Preserve transparent paired A/B results in workflow logs and Job Summary; a single live sample is evidence for that run only, not a general performance guarantee.

The latest validated live cascade passed 6/6 tasks, with 4/6 solved on tier-0. The paired one-repeat benchmark recorded PICK 6/6 and classic Python 5/6 on the same model; it is a small sample and does not establish a general speed or cost advantage. Live evaluation consumes provider quota.

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
