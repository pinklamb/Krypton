"""Measure what agent work costs on a given copy of a repository (for example one rebuilt with Krypton): the same tickets the earlier control runs did, once, with a spend cap.

    python -m tools.measure_on_tree --yes --max-usd 0.5 --tree <copy with src/ and tests/> --python <venv python> --workspaces <dir> --out <file>

Every request's usage is saved with the structure score after each ticket, and the hidden acceptance tests and the project's own suite are run after every ticket.
The key is read from the environment or .env and never reaches the tools.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from tools import agent_cost as ac
from tools.cache_anthropic import MODEL, PRICES, Budget, BudgetExceeded, find_key


def main(args: argparse.Namespace) -> int:
    """Run the tickets in order on a fresh workspace made from the tree."""
    workspace = ac.fresh_workspace(args.tree, args.workspaces / "measure_tree", args.python)
    start = ac.code_stats(workspace)
    print(f"tickets {args.tasks} on {args.tree}; model {MODEL}; budget ${args.max_usd:.2f}; start {start}")
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

    client, budget, stages = anthropic.Anthropic(api_key=key, max_retries=6), Budget(args.max_usd), []

    def save() -> None:
        meta = {"date": date.today().isoformat(), "model": MODEL, "prices_per_mtok": PRICES, "spent_usd": round(budget.spent, 6), "max_usd": budget.cap, "tasks": args.tasks, "tree": str(args.tree)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"meta": meta, "start": start, "stages": stages}, indent=1), encoding="utf-8")

    try:
        for number in args.tasks:
            stage = ac.run_stage(client, workspace, f"ticket {number}", ac.TICKETS[number], ac.BASE_PROMPT, ac.TOOLS, 45, budget, args.hidden)
            stages.append({**stage, "label": f"ticket {number}", "kind": "ticket"})
            t = stage["totals"]
            print(f"  ticket {number:<3} {t['requests']:>3} requests  ${t['cost']:.3f}  cached {t['cached_share']:.0%}  purity {stage['after']['purity']}  stop={stage['stop']}  total ${budget.spent:.2f}", flush=True)
            save()
            if stage["stop"].startswith("budget"):
                raise BudgetExceeded(stage["stop"])
    except BudgetExceeded as stop:
        print(f"stopped by the budget guard: {stop}")
    save()
    print(f"\ntotal spend ${budget.spent:.4f} of ${args.max_usd:.2f}; saved to {args.out}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measure agent cost on a copy of a repository.")
    parser.add_argument("--tasks", default="2,7,8,9,10,11", help="ticket numbers, comma separated")
    parser.add_argument("--max-usd", type=float, default=0.5)
    parser.add_argument("--tree", type=Path, required=True, help="a copy of the repository with src/ and tests/")
    parser.add_argument("--hidden", type=Path, default=Path(__file__).parent / "garth_acceptance" / "hidden_tests.py")
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--workspaces", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env-root", type=Path, default=ac.ROOT)
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--yes", action="store_true")
    options = parser.parse_args()
    options.tasks = [int(part) for part in options.tasks.split(",") if part]
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(options))
