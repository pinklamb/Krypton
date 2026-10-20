"""Rebuild a repository with Krypton: refactor the worst functions first until the structure score reaches a target, then measure what agent work costs on the result.

    python -m tools.rebuild_with_krypton --dry ...
    python -m tools.rebuild_with_krypton --yes --target 75 --max-usd 1.4 ...

One agent is given the run_krypton tool and told to take the worst function, split it, re-score, and continue down the list; it stops at the target. If a round ends below the target the
agent is sent back with the current score (up to --rounds rounds). The refactored tree is saved, the project's own tests are run, and then fresh agents (no Krypton) do the feature
tickets on it, so the cost of iterating on the rebuilt code can be set beside the same tickets on the original. Every request's usage is saved; the key never reaches the tools.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from tools import agent_cost as ac
from tools.cache_anthropic import MODEL, PRICES, Budget, BudgetExceeded, find_key

REBUILD_TICKET = """Rebuild task: make this codebase healthier with Krypton, worst functions first.
Run the run_krypton tool on 'src' to see the purity score and the worst functions. Refactor the worst function first (split it into small named helpers, use early returns, flatten
deep nesting), run the tests, run Krypton again, then take the next worst function, and keep going down the list. Behaviour and public names must stay the same and every existing test
must keep passing. Stop as soon as the purity score is {target} or higher; do not keep refactoring past that. When done reply with DONE and the final purity score."""
CONTINUE_TICKET = """Continue the rebuild: the purity score is now {score} and the target is {target}. Run run_krypton on 'src', take the next worst functions, refactor them the same way (same behaviour,
all tests passing), and stop as soon as the score is {target} or higher. Reply with DONE and the final purity score."""
SYSTEM = ac.BASE_PROMPT + "\nKrypton is available through the run_krypton tool."


def rebuild(client, workspace: ac.Workspace, target: int, rounds: int, budget: Budget, hidden: Path, report) -> list[dict]:
    """Run refactor rounds until the score reaches the target, a round hits the budget, or the rounds run out."""
    out: list[dict] = []
    for number in range(1, rounds + 1):
        score = ac.code_stats(workspace)["purity"]
        if score >= target:
            break
        text = (REBUILD_TICKET if number == 1 else CONTINUE_TICKET).format(target=target, score=score)
        stage = ac.run_stage(client, workspace, f"rebuild round {number}", text, SYSTEM, ac.TOOLS + [ac.KRYPTON_TOOL], 70, budget, hidden, check=False)
        out.append({**stage, "label": f"round {number}", "kind": "rebuild", "score_before": score})
        report(f"round {number}", stage, budget)
        if stage["stop"].startswith("budget"):
            raise BudgetExceeded(stage["stop"])
    return out


def main(args: argparse.Namespace) -> int:
    """Plan, then run the rebuild and the measurement tickets."""
    print(f"rebuild to a purity score of {args.target}, then tickets {args.tasks}; model {MODEL}; budget ${args.max_usd:.2f}")
    workspace = ac.fresh_workspace(args.source, args.workspaces / "rebuild", args.python)
    start = ac.code_stats(workspace)
    print("start:", start)
    if args.dry:
        return 0
    key = find_key(args.env_root)
    if not key:
        print("no Anthropic key found", file=sys.stderr)
        return 2
    if not args.yes:
        print("re-run with --yes to spend real money", file=sys.stderr)
        return 2
    import anthropic

    client, budget = anthropic.Anthropic(api_key=key, max_retries=6), Budget(args.max_usd)
    record: dict = {"start": start, "rebuild": [], "tickets": [], "target": args.target}

    def save() -> None:
        meta = {"date": date.today().isoformat(), "model": MODEL, "prices_per_mtok": PRICES, "spent_usd": round(budget.spent, 6), "max_usd": budget.cap, "tasks": args.tasks}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"meta": meta, **record}, indent=1), encoding="utf-8")

    def report(label: str, stage: dict, spent: Budget) -> None:
        t = stage["totals"]
        print(f"  {label:<18} {t['requests']:>3} requests  ${t['cost']:.3f}  purity {stage['after']['purity']}  stop={stage['stop']}  total ${spent.spent:.2f}", flush=True)
        save()

    try:
        record["rebuild"] = rebuild(client, workspace, args.target, args.rounds, budget, args.hidden, report)
        record["after_rebuild"] = ac.code_stats(workspace)
        record["rebuild_check"] = ac.check_work(workspace, args.hidden)
        shutil.copytree(workspace.root / "src", args.save_tree, dirs_exist_ok=True)
        print("rebuilt:", record["after_rebuild"], "| garth suite ok:", record["rebuild_check"].get("suite_ok"), "| saved to", args.save_tree)
        save()
        for number in args.tasks:
            stage = ac.run_stage(client, workspace, f"ticket {number}", ac.TICKETS[number], ac.BASE_PROMPT, ac.TOOLS, 45, budget, args.hidden)
            record["tickets"].append({**stage, "label": f"ticket {number}", "kind": "ticket"})
            report(f"ticket {number}", stage, budget)
            if stage["stop"].startswith("budget"):
                raise BudgetExceeded(stage["stop"])
    except BudgetExceeded as stop:
        print(f"stopped by the budget guard: {stop}")
    save()
    print(f"\ntotal spend ${budget.spent:.4f} of ${args.max_usd:.2f}; saved to {args.out}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild a repository with Krypton until its structure score reaches a target, then measure.")
    parser.add_argument("--target", type=int, default=75, help="purity score (0 to 100) to reach")
    parser.add_argument("--rounds", type=int, default=3, help="most refactor rounds")
    parser.add_argument("--tasks", default="2,7,8,9,10,11", help="tickets to run on the rebuilt code, comma separated")
    parser.add_argument("--max-usd", type=float, default=1.4)
    parser.add_argument("--source", type=Path, required=True, help="a clean copy of garth (with tests)")
    parser.add_argument("--hidden", type=Path, default=Path(__file__).parent / "garth_acceptance" / "hidden_tests.py")
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--workspaces", type=Path, required=True)
    parser.add_argument("--save-tree", type=Path, required=True, help="where the rebuilt src/ is copied")
    parser.add_argument("--out", type=Path, default=ac.ROOT / "cache_anthropic_results" / f"rebuild_garth_{date.today().isoformat()}.json")
    parser.add_argument("--env-root", type=Path, default=ac.ROOT)
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--yes", action="store_true")
    options = parser.parse_args()
    options.tasks = [int(part) for part in options.tasks.split(",") if part]
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(options))
