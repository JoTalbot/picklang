# Paired live A/B report

`bench_ab.py` compares recorded PICK and classic-agent runs on the same task set. It makes no API calls and never guesses provider prices.

Each input is a JSON array of rows with a unique `task` name and required fields `ok` (boolean), `model` (non-empty string), `attempts` (non-negative integer), `seconds` (finite non-negative number), `prompt_tokens` and `completion_tokens` (non-negative integers). Missing or malformed measurements are rejected; they are never silently converted to zero. The report also refuses to compare different task sets. Cost is left unknown unless valid prices are supplied for every model.

```bash
python bench_ab.py --pick bench/pick.json --baseline bench/classic.json --write bench/ab-report.md
```

For estimated cost, supply separate JSON pricing maps in USD per million tokens, e.g. `{"provider/model-id":{"prompt_per_million":0.1,"completion_per_million":0.4}}`, using the provider's current rates:

```bash
python bench_ab.py --pick bench/pick.json --baseline bench/classic.json --pick-prices bench/pick-prices.json --baseline-prices bench/classic-prices.json --write bench/ab-report.md
```

Cost is shown only when every model has a rate. Summed per-task durations are not necessarily wall-clock elapsed time if tasks ran concurrently. Passing a validator does not prove equivalent user-facing quality; repeat runs and inspect outputs before claiming a general advantage.
