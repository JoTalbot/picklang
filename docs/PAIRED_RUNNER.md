# Executable paired A/B runner

`bench_pair.py` executes two adapter commands against the same task fixtures. Unlike `bench_ab.py`, which summarizes existing result files, this tool runs both sides and records per-invocation timing, self-reported success, output, model, token counts, attempts, and an input digest.

## Adapter protocol

Each adapter is started as an argv command (quoted with shell-style syntax, but **no shell is invoked**). It receives one JSON object on stdin:

```json
{"task":"sum-two","input":{"a":2,"b":3}}
```

It must print one JSON object to stdout, including a boolean `ok`. Recommended response:

```json
{"ok":true,"output":5,"model":"provider/model","prompt_tokens":120,"completion_tokens":8,"attempts":1}
```

Use the same task-specific validator in both adapters. The runner cannot independently determine whether arbitrary output is correct; `ok` is the adapter's validation result. Keep debug logs on stderr, not stdout. Provider pricing must be measured or supplied separately; token counts alone do not establish billed cost.

## Fixture and invocation

Create `bench/tasks.json`:

```json
[
  {"task":"sum-two","input":{"a":2,"b":3}},
  {"task":"extract-name","input":{"text":"Hello, Ada","expected":"Ada"}}
]
```

Run adapter commands and save a reproducible result file:

```bash
python bench_pair.py \
  --tasks bench/tasks.json \
  --pick-command "python adapters/pick_adapter.py" \
  --baseline-command "python adapters/classic_adapter.py" \
  --repeats 3 --timeout 60 \
  --write bench/paired-results.json
```

Execution order alternates to reduce systematic first-run bias. Each invocation has an independent timeout. The JSON includes both raw outputs for review, input digests, repeat numbers, per-call elapsed seconds, and error details. Repeat runs are important because provider latency and model output are stochastic.

This is a generic adapter harness, not a claim that PICK wins. It does not itself implement a model agent or invent API pricing. Use adapters backed by the actual PICK and classic workflows, with identical provider/model settings where the comparison is intended to isolate orchestration overhead.
