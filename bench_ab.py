#!/usr/bin/env python3
"""Compare paired agent-evaluation JSON runs; no API calls or guessed prices."""
import argparse, json
from pathlib import Path


def load_rows(path):
    raw=json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw,list) or not raw: raise ValueError(f"{path}: expected non-empty JSON array")
    rows={}
    for row in raw:
        if not isinstance(row,dict) or not isinstance(row.get("task"),str) or not row["task"]:
            raise ValueError(f"{path}: every row needs a task name")
        name=row["task"]
        if name in rows: raise ValueError(f"{path}: duplicate task {name!r}")
        for field in ("prompt_tokens","completion_tokens","attempts","seconds"):
            value=row.get(field,0)
            if not isinstance(value,(int,float)) or isinstance(value,bool) or value<0:
                raise ValueError(f"{path}: {name}.{field} must be non-negative")
        rows[name]=row
    return rows


def estimate_cost(row, prices):
    if prices is None: return None
    price=prices.get(row.get("model"))
    if not isinstance(price,dict): return None
    rates=(price.get("prompt_per_million"),price.get("completion_per_million"))
    if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and v>=0 for v in rates):
        raise ValueError(f"invalid pricing for model {row.get('model')!r}")
    return (row.get("prompt_tokens",0)*rates[0]+row.get("completion_tokens",0)*rates[1])/1_000_000


def summarize(rows, prices=None):
    n=len(rows); passed=sum(bool(r.get("ok")) for r in rows.values())
    costs=[estimate_cost(r,prices) for r in rows.values()]
    prompt=sum(r.get("prompt_tokens",0) for r in rows.values())
    completion=sum(r.get("completion_tokens",0) for r in rows.values())
    return {"tasks":n,"passed":passed,"pass_rate":passed/n,
      "attempts":sum(r.get("attempts",0) for r in rows.values()),
      "wall_seconds_sum":sum(r.get("seconds",0) for r in rows.values()),
      "prompt_tokens":prompt,"completion_tokens":completion,"total_tokens":prompt+completion,
      "estimated_cost_usd":sum(costs) if all(c is not None for c in costs) else None}


def compare(pick,baseline,pick_prices=None,baseline_prices=None):
    if set(pick)!=set(baseline):
        raise ValueError(f"task sets differ; missing from PICK={sorted(set(baseline)-set(pick))}, missing from baseline={sorted(set(pick)-set(baseline))}")
    return {"tasks":[{"task":k,"pick_ok":bool(pick[k].get("ok")),"baseline_ok":bool(baseline[k].get("ok")),
      "pick_model":pick[k].get("model"),"baseline_model":baseline[k].get("model")} for k in pick],
      "pick":summarize(pick,pick_prices),"baseline":summarize(baseline,baseline_prices),
      "warning":"Cost is estimated only when prices are supplied for every model. Token usage is not semantic equivalence; compare outputs and repeat runs before claiming general superiority."}


def markdown(report):
    p,b=report["pick"],report["baseline"]
    lines=["# Paired live A/B evaluation","","| Metric | PICK | Classic baseline |","|---|---:|---:|",
      f"| Tasks passed | {p['passed']}/{p['tasks']} | {b['passed']}/{b['tasks']} |",
      f"| Pass rate | {p['pass_rate']:.1%} | {b['pass_rate']:.1%} |",
      f"| Attempts | {p['attempts']} | {b['attempts']} |",
      f"| Sum of per-task durations (s) | {p['wall_seconds_sum']:.2f} | {b['wall_seconds_sum']:.2f} |",
      f"| Prompt tokens | {p['prompt_tokens']} | {b['prompt_tokens']} |",
      f"| Completion tokens | {p['completion_tokens']} | {b['completion_tokens']} |",
      f"| Total tokens | {p['total_tokens']} | {b['total_tokens']} |"]
    if p["estimated_cost_usd"] is not None and b["estimated_cost_usd"] is not None:
        lines.append(f"| Estimated API cost (USD) | {p['estimated_cost_usd']:.6f} | {b['estimated_cost_usd']:.6f} |")
    lines += ["","## Per-task parity","","| Task | PICK | Baseline | PICK model | Baseline model |","|---|---|---|---|---|"]
    lines += [f"| {r['task']} | {'PASS' if r['pick_ok'] else 'FAIL'} | {'PASS' if r['baseline_ok'] else 'FAIL'} | {r['pick_model'] or 'n/a'} | {r['baseline_model'] or 'n/a'} |" for r in report["tasks"]]
    lines += ["","> Limitation: "+report["warning"],""]
    return "\n".join(lines)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pick",required=True); ap.add_argument("--baseline",required=True)
    ap.add_argument("--pick-prices"); ap.add_argument("--baseline-prices"); ap.add_argument("--write")
    a=ap.parse_args()
    prices=lambda p: json.loads(Path(p).read_text(encoding="utf-8")) if p else None
    output=markdown(compare(load_rows(a.pick),load_rows(a.baseline),prices(a.pick_prices),prices(a.baseline_prices)))
    print(output)
    if a.write: Path(a.write).write_text(output,encoding="utf-8")
    return 0
if __name__=="__main__": raise SystemExit(main())
