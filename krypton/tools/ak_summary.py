"""Summarize an A (no Krypton) vs K (Krypton tool) agent-cost run: cost, tokens, correctness and structure per arm, paired by repetition.

    python -m tools.ak_summary cache_anthropic_results/agent_ak_2026-10-19.json
"""

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path


def summarize(data: dict) -> dict:
    """Per-arm averages over the repetitions where both arms finished every ticket, plus the paired cost difference."""
    arms, tasks = data["arms"], data["meta"]["tasks"]
    reps = sorted({v["rep"] for v in arms.values()})
    done = [r for r in reps if all(f"{a}{r}" in arms and len(arms[f"{a}{r}"]["stages"]) == len(tasks) and all(s["stop"] == "end_turn" for s in arms[f"{a}{r}"]["stages"]) for a in "AK")]
    out = {"reps": len(done), "arms": {}}
    for a in "AK":
        runs = [arms[f"{a}{r}"]["stages"] for r in done]
        cost = [sum(s["totals"]["cost"] for s in stages) for stages in runs]
        row = {"cost": st.mean(cost), "cost_sd": st.pstdev(cost), "per_rep": cost,
               "per_ticket": [st.mean(stages[i]["totals"]["cost"] for stages in runs) for i in range(len(tasks))],
               "prompt_tokens": st.mean(sum(s["totals"][k] for s in stages for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) for stages in runs),
               "requests": st.mean(sum(s["totals"]["requests"] for s in stages) for stages in runs),
               "cached_share": st.mean(s["totals"]["cached_share"] for stages in runs for s in stages),
               "hidden_pass": st.mean(sum(s["check"][f"hidden_t{int(s['label'].split()[1])}"] for s in stages) for stages in runs),
               "tickets_all_pass": sum(all(s["check"][f"hidden_t{int(s['label'].split()[1])}"] for s in stages) for stages in runs),
               "suite_ok": sum(stages[-1]["check"]["suite_ok"] for stages in runs),
               "purity": st.mean(stages[-1]["after"]["purity"] for stages in runs), "nested": st.mean(stages[-1]["after"]["nested_functions"] for stages in runs),
               "flagged": st.mean(stages[-1]["after"]["flagged_files"] for stages in runs),
               "krypton_calls": st.mean(sum(s["tool_calls"].get("run_krypton", 0) for s in stages) for stages in runs)}
        out["arms"][a] = row
    diff = [k - a for k, a in zip(out["arms"]["K"]["per_rep"], out["arms"]["A"]["per_rep"])]
    out["paired_diff_mean"], out["paired_diff_sd"], out["k_cheaper_in"] = st.mean(diff), st.stdev(diff) if len(diff) > 1 else 0.0, sum(d < 0 for d in diff)
    out["start_purity"] = st.mean(arms[f"A{r}"]["start"]["purity"] for r in done)
    return out


if __name__ == "__main__":
    s = summarize(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    print(f"complete paired repetitions: {s['reps']}; start purity {s['start_purity']:.0f}")
    for a, r in s["arms"].items():
        print(f"{a}: ${r['cost']:.3f}/rep (sd {r['cost_sd']:.3f}) tokens {r['prompt_tokens'] / 1e3:.0f}k req {r['requests']:.1f} cached {r['cached_share']:.0%} hidden {r['hidden_pass']:.1f}/6 "
              f"all-pass {r['tickets_all_pass']}/{s['reps']} suite {r['suite_ok']}/{s['reps']} purity {r['purity']:.0f} nested {r['nested']:.1f} flagged {r['flagged']:.1f} krypton calls {r['krypton_calls']:.1f}")
        print("   per ticket", [round(x, 3) for x in r["per_ticket"]])
    print(f"paired K-A: {s['paired_diff_mean']:+.3f}/rep (sd {s['paired_diff_sd']:.3f}), K cheaper in {s['k_cheaper_in']}/{s['reps']}")
