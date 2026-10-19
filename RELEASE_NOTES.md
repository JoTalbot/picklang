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
