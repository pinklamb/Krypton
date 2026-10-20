"""Turn a refactor-ladder results file into the comparison: cost of the later tickets per branch, the cleanup's payback, and what it did to the code.

    python -m tools.ladder_summary cache_anthropic_results/ladder_2026-10-20.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def mean(values: list[float]) -> float:
    """Average, 0 for an empty list."""
    return sum(values) / len(values) if values else 0.0


def complete(rep: dict, later: int, variants: list[str]) -> bool:
    """A repetition counts only when its shared ticket and every branch ran to the end without a budget stop or a turn limit."""
    if "shared" not in rep or rep["shared"]["stop"] != "end_turn":
        return False
    expected = {"control": later, **{v: later + 1 for v in variants}}
    for name, size in expected.items():
        stages = rep["branches"].get(name, {}).get("stages", [])
        if len(stages) != size or any(s["stop"] != "end_turn" for s in stages):
            return False
    return True


def branch_row(reps: list[dict], name: str, control: list[float]) -> dict:
    """One branch's averages over the complete repetitions."""
    tickets = [[s for s in r["branches"][name]["stages"] if s["kind"] == "ticket"] for r in reps]
    cleanups = [next((s for s in r["branches"][name]["stages"] if s["kind"] == "cleanup"), None) for r in reps]
    totals = [sum(s["totals"]["cost"] for s in ts) for ts in tickets]
    cleanup_cost = mean([c["totals"]["cost"] if c else 0.0 for c in cleanups])
    saving = mean([c - t for c, t in zip(control, totals)]) / max(1, len(tickets[0]))
    final = [ts[-1] for ts in tickets]
    row = {
        "reps": len(reps), "later_cost": mean(totals), "per_ticket": [mean([ts[i]["totals"]["cost"] for ts in tickets]) for i in range(len(tickets[0]))],
        "cleanup_cost": cleanup_cost, "saving_per_ticket": saving, "payback_tickets": cleanup_cost / saving if saving > 0 and cleanup_cost else None,
        "cheaper_in": sum(1 for c, t in zip(control, totals) if t < c), "prompt_tokens_per_ticket": mean([sum(s["totals"][k] for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) for ts in tickets for s in ts]),
        "requests_per_ticket": mean([s["totals"]["requests"] for ts in tickets for s in ts]), "purity_final": mean([f["after"]["purity"] for f in final]),
        "nested_final": mean([f["after"]["nested_functions"] for f in final]), "flagged_final": mean([f["after"]["flagged_files"] for f in final]),
        "suite_ok": sum(1 for f in final if f["check"]["suite_ok"]),
    }
    cleaned = [c for c in cleanups if c]
    if cleaned:
        row["purity_after_cleanup"] = mean([c["after"]["purity"] for c in cleaned])
        row["nested_after_cleanup"] = mean([c["after"]["nested_functions"] for c in cleaned])
        row["tokens_after_cleanup"] = mean([c["after"]["tokens_est"] for c in cleaned])
    return row


def summarize(data: dict) -> dict:
    """Per-branch rows over the complete repetitions, plus how many repetitions were used."""
    variants, tasks = list(data["meta"]["variants"]), data["meta"]["tasks"]
    reps = [r for r in data["reps"].values() if complete(r, len(tasks) - 1, variants)]
    if not reps:
        return {"reps": 0, "branches": {}}
    control = [sum(s["totals"]["cost"] for s in r["branches"]["control"]["stages"]) for r in reps]
    shared = mean([r["shared"]["totals"]["cost"] for r in reps])
    rows = {name: branch_row(reps, name, control) for name in ("control", *variants)}
    return {"reps": len(reps), "shared_cost": shared, "branches": rows}


def show(summary: dict) -> None:
    """A plain table."""
    print(f"complete repetitions: {summary['reps']}; shared first ticket averaged ${summary.get('shared_cost', 0):.3f}")
    print(f"{'branch':<10} {'later $':>8} {'per-ticket saving':>18} {'cleanup $':>10} {'payback':>8} {'cheaper in':>11} {'purity':>7} {'nested':>7}")
    for name, row in summary["branches"].items():
        payback = f"{row['payback_tickets']:.0f} tkts" if row["payback_tickets"] else "-"
        print(f"{name:<10} {row['later_cost']:>8.3f} {row['saving_per_ticket']:>+18.4f} {row['cleanup_cost']:>10.3f} {payback:>8} {row['cheaper_in']:>6}/{row['reps']:<4} {row['purity_final']:>7.0f} {row['nested_final']:>7.1f}")


if __name__ == "__main__":
    show(summarize(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))))
