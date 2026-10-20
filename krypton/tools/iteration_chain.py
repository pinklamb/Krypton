"""A long iteration chain, with and without a Krypton CI gate: does the cost of each revision change as one feature grows over 20 revisions?

    python -m tools.iteration_chain --dry ...                          # the plan and what the gate sees at the start, no API calls
    python -m tools.iteration_chain --yes --reps 3 --max-usd 6 ...

One agent per ticket works the 20 revisions of the Body Battery event query one after another on the same code (tickets 2, 7 to 25). Two arms:

  plain   the agent just does each ticket
  gate    after each ticket the harness scores the code with Krypton, and if a function the ticket touched is over the structure limit it sends an agent back with Krypton's
          report to fix it before the next ticket (up to --max-fixes times). The agent is never given the Krypton tool and is never told to refactor in advance.

The cost of every agent run is recorded, so the gate arm pays for its own fix rounds. For each ticket the record holds cost, requests, hidden-test results, the structure score, and
how far over the limit the worst function (and the feature's own worst function) is, so cost per revision can be plotted against the revision number for both arms.
The key is read from the environment or .env and never reaches the tools.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from krypton.engine.run import run_local
from tools import agent_cost as ac
from tools.cache_anthropic import MODEL, PRICES, Budget, BudgetExceeded, find_key
from tools.refactor_ladder import parse_tasks

CHAIN = [2, *range(7, 26)]   # ticket 2 (end time), then 7 to 25: twenty revisions of one feature
FEATURE_FILE = "src/garth/data/body_battery/events.py"
FIX_TICKET = """CI check failed on your last change. Krypton scored the files you changed and found functions you touched that are over the structure limit:
{lines}
Fix them without changing behaviour or any public name: split each one into small named helpers (one job each), use early returns and flatten deep nesting. Every existing test must keep passing. When done reply with DONE."""
PER_TICKET = {"plain": 0.04, "gate": 0.065}   # dollars, rough, from the earlier chains; only used for the printed estimate

Ratings = dict[tuple[str, str], float]


def ratings(workspace: ac.Workspace) -> tuple[Ratings, float]:
    """Every function's rating in the source tree, keyed by (file, qualified name), and the flag threshold."""
    sources = {p.relative_to(workspace.root).as_posix(): p.read_bytes() for p in (workspace.root / "src").rglob("*.py")}
    result = run_local(sources)
    return {(f.filename, fn.name): fn.rating_master for f in result.findings for fn in f.functions}, result.threshold


def gate_offenders(before: Ratings, after: Ratings, threshold: float) -> list[tuple[str, str, float]]:
    """Functions the change touched (new, or with a different rating) that are over the threshold, worst first. Untouched tangle does not fail the gate."""
    touched = {key: rating for key, rating in after.items() if key not in before or before[key] != rating}
    return sorted(((file, name, rating) for (file, name), rating in touched.items() if rating > threshold), key=lambda item: -item[2])


def gate_text(offenders: list[tuple[str, str, float]], threshold: float) -> str:
    """The failure report an agent is sent."""
    return FIX_TICKET.format(lines="\n".join(f"- {file}: {name} ({rating / threshold:.1f}x over the limit)" for file, name, rating in offenders[:8]))


def worst_over(after: Ratings, threshold: float, only: str | None = None) -> float:
    """How many times over the limit the worst function is (optionally only within one file)."""
    values = [rating for (file, _), rating in after.items() if only is None or file == only]
    return round(max(values, default=0.0) / threshold, 1)


def record_stage(stage: dict, label: str, kind: str) -> dict:
    """Name a stage record."""
    return {**stage, "label": label, "kind": kind}


def run_ticket(arm: str, rep: int, number: int, client, workspace: ac.Workspace, args, budget: Budget) -> dict:
    """One ticket, then (gate arm) the fix rounds the gate asks for, then the measurements. Raises BudgetExceeded after recording a budget stop."""
    before, threshold = ratings(workspace)
    stages = [record_stage(ac.run_stage(client, workspace, f"{arm}{rep}/ticket {number}", ac.TICKETS[number], ac.BASE_PROMPT, ac.TOOLS, 45, budget, args.hidden, check=False), f"ticket {number}", "ticket")]
    after, _ = ratings(workspace)
    offenders = gate_offenders(before, after, threshold)
    gate = {"offenders_after_ticket": len(offenders), "fixes": 0, "offenders_left": len(offenders)}
    while arm == "gate" and offenders and gate["fixes"] < args.max_fixes and not stages[-1]["stop"].startswith("budget"):
        gate["fixes"] += 1
        stages.append(record_stage(ac.run_stage(client, workspace, f"{arm}{rep}/ticket {number} fix {gate['fixes']}", gate_text(offenders, threshold), ac.BASE_PROMPT, ac.TOOLS, 45, budget, args.hidden, check=False),
                                   f"ticket {number} fix {gate['fixes']}", "ci_fix"))
        after, _ = ratings(workspace)
        offenders = gate_offenders(before, after, threshold)
        gate["offenders_left"] = len(offenders)
    totals = {key: sum(s["totals"][key] for s in stages) for key in ("cost", "requests", "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    return {"ticket": number, "stages": stages, "totals": totals, "gate": gate, "stop": stages[-1]["stop"], "after": ac.code_stats(workspace), "check": ac.check_work(workspace, args.hidden),
            "worst_x": worst_over(after, threshold), "feature_worst_x": worst_over(after, threshold, FEATURE_FILE)}


def run_chain(arm: str, rep: int, client, args, budget: Budget, results: dict) -> None:
    """All the tickets for one arm of one repetition, saving after each."""
    workspace = ac.fresh_workspace(args.source, args.workspaces / f"chain{rep}_{arm}", args.python)
    start_ratings, threshold = ratings(workspace)
    record = results.setdefault(f"{arm}{rep}", {"arm": arm, "rep": rep, "start": ac.code_stats(workspace) | {"worst_x": worst_over(start_ratings, threshold), "feature_worst_x": worst_over(start_ratings, threshold, FEATURE_FILE)}, "tickets": []})
    for number in args.tasks:
        done = run_ticket(arm, rep, number, client, workspace, args, budget)
        record["tickets"].append(done)
        save(args, results, budget)
        t = done["totals"]
        print(f"  {arm}{rep} ticket {number:<3} {t['requests']:>3} requests  ${t['cost']:.3f}  fixes {done['gate']['fixes']}  purity {done['after']['purity']}  worst {done['worst_x']}x  total ${budget.spent:.2f}", flush=True)
        if done["stop"].startswith("budget"):
            raise BudgetExceeded(done["stop"])


def save(args, results: dict, budget: Budget) -> None:
    """Write everything so far (no key, no prompts, no file contents)."""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"date": date.today().isoformat(), "model": MODEL, "prices_per_mtok": PRICES, "spent_usd": round(budget.spent, 6), "max_usd": budget.cap, "tasks": args.tasks, "arms": args.arms, "max_fixes": args.max_fixes}
    args.out.write_text(json.dumps({"meta": meta, "chains": results}, indent=1), encoding="utf-8")


def estimate(tasks: list[int], arms: list[str]) -> float:
    """A rough cost per repetition."""
    return sum(PER_TICKET[arm] * len(tasks) for arm in arms)


def main(args: argparse.Namespace) -> int:
    """Plan, then run the repetitions with the arms interleaved (and the order alternated) so a stop leaves them balanced."""
    print(f"{len(args.tasks)} tickets {args.tasks}; arms {args.arms}; model {MODEL}; budget ${args.max_usd:.2f}; about ${estimate(args.tasks, args.arms):.2f} per repetition")
    if args.dry:
        workspace = ac.fresh_workspace(args.source, args.workspaces / "chain_dry", args.python)
        found, threshold = ratings(workspace)
        print(f"at the start {sum(1 for r in found.values() if r > threshold)} of {len(found)} functions are over the limit; the feature file's worst is {worst_over(found, threshold, FEATURE_FILE)}x")
        return 0
    key = find_key(args.env_root)
    if not key:
        print("no Anthropic key found", file=sys.stderr)
        return 2
    if not args.yes:
        print("re-run with --yes to spend real money", file=sys.stderr)
        return 2
    import anthropic

    client, budget, results = anthropic.Anthropic(api_key=key, max_retries=6), Budget(args.max_usd), {}
    try:
        for rep in range(args.rep_start, args.rep_start + args.reps):
            for arm in (args.arms if rep % 2 == 0 else args.arms[::-1]):
                run_chain(arm, rep, client, args, budget, results)
    except BudgetExceeded as stop:
        print(f"stopped by the budget guard: {stop}")
    print(f"\ntotal spend ${budget.spent:.4f} of ${args.max_usd:.2f}; saved to {args.out}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Twenty revisions of one feature, with and without a Krypton CI gate.")
    parser.add_argument("--tasks", default=",".join(map(str, CHAIN)), help="ticket numbers, comma separated (default: the twenty-revision chain)")
    parser.add_argument("--arms", default="plain,gate")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--rep-start", type=int, default=0)
    parser.add_argument("--max-fixes", type=int, default=2, help="most fix rounds the gate asks for per ticket")
    parser.add_argument("--max-usd", type=float, default=6.0)
    parser.add_argument("--source", type=Path, required=True, help="a clean copy of garth (with tests)")
    parser.add_argument("--hidden", type=Path, default=Path(__file__).parent / "garth_acceptance" / "hidden_tests.py")
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--workspaces", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ac.ROOT / "cache_anthropic_results" / f"iteration_chain_{date.today().isoformat()}.json")
    parser.add_argument("--env-root", type=Path, default=ac.ROOT)
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--yes", action="store_true")
    options = parser.parse_args()
    options.tasks = parse_tasks(options.tasks)
    options.arms = [a for a in options.arms.split(",") if a]
    unknown = [a for a in options.arms if a not in PER_TICKET]
    if unknown:
        parser.error(f"unknown arms: {unknown}")
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(options))
