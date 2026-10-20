"""The refactor ladder: is the next change cheaper for an agent after Krypton has cleaned the code up?

    python -m tools.refactor_ladder --dry ...                       # the plan, the targeted files and an estimate, no API calls
    python -m tools.refactor_ladder --max-usd 20 --reps 5 --yes ...
    python -m tools.refactor_ladder --tasks 2,7,8,9,10,11 ...      # the feature-iteration test: one feature revised six times

For each repetition, one agent works ticket 1 on the regular code, and that cost is the shared starting point. The code is then
copied into branches that continue from exactly the same state:

  control    no cleanup; tickets 2 to 6 on the code as it is
  standard   Krypton cleans the whole source tree (default threshold), then the same tickets
  strict     the same with a stricter threshold (half the default)
  relaxed    the same with a looser threshold (three times the default)
  targeted   only the files that git history shows are changed most often, and that Krypton flags, are cleaned, then the same tickets

Every agent sees only its ticket; Krypton is used by the cleanup agents only. Every request's usage is saved, so a branch's cost per
ticket, the cost of its cleanup, and the code's structure score afterwards are measured. Hidden acceptance tests and garth's own
suite are run after every stage. The cost of iteration N on cleaned code against the control, over the same ticket, is the answer;
the variants show which Krypton settings pay off. The key is read from the environment or .env and never reaches the tools.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from krypton.history import commit_counts
from tools import agent_cost as ac
from tools.cache_anthropic import MODEL, PRICES, Budget, BudgetExceeded, find_key

VARIANTS = {
    "standard": {"level": "standard", "scope": "all"},
    "strict": {"level": "strict", "scope": "all"},
    "relaxed": {"level": "relaxed", "scope": "all"},
    "targeted": {"level": "standard", "scope": "hot"},
}
CLEANUP_TARGETED = """Refactor ticket (targeted): make these files easier to read and change without changing behavior:
{files}
They are the files changed most often in the project's git history. Run the run_krypton tool on them, split every flagged function into small named helpers (early returns, one job per function), flatten deep nesting, and run Krypton again until nothing in them is flagged or marked nested. Do not touch other files. Public names, signatures and behavior must stay the same and every existing test must keep passing. When done reply with DONE and the final purity score for these files."""
CLEANUP_SYSTEM = ac.BASE_PROMPT + "\nKrypton is available through the run_krypton tool."
PER_TICKET, PER_CLEANUP = 0.045, 0.40  # dollars, from the earlier agent-cost measurements; only used for the printed estimate


def parse_tasks(text: str) -> list[int]:
    """Ticket numbers from '123456' (single digits) or '2,7,8,9,10,11' (any numbers, comma separated)."""
    return [int(part) for part in text.split(",")] if "," in text else [int(c) for c in text]


def flagged_files(workspace: ac.Workspace) -> set[str]:
    """Source files (relative to the workspace) that Krypton flags or that hold a deeply nested function."""
    sys.path.insert(0, str(ac.KRYPTON_SRC))
    from krypton.engine.run import run_local

    sources = {p.relative_to(workspace.root).as_posix(): p.read_bytes() for p in (workspace.root / "src").rglob("*.py")}
    result = run_local(sources)
    return {f.filename for f in result.findings if f.flagged or any(fn.nested for fn in f.functions)}


def hot_files(history_repo: Path, subdir: str, workspace: ac.Workspace, top: int, window: int = 200) -> list[str]:
    """The most often changed files in git history that Krypton flags now, as workspace paths; history only, nothing from later tickets."""
    counts = commit_counts(history_repo / subdir, window)
    flagged = flagged_files(workspace)
    ranked = sorted(((count, f"{subdir}/{name}") for name, count in counts.items() if f"{subdir}/{name}" in flagged), key=lambda item: (-item[0], item[1]))
    return [name for _, name in ranked[:top]]


def branch_from(base: ac.Workspace, destination: Path, level: str = "standard") -> ac.Workspace:
    """A copy of the workspace as it is now, so every branch continues from identical code."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(base.root, destination)
    return ac.Workspace(destination, base.python, level)


def cleanup_ticket(variant: dict, targets: list[str]) -> str:
    """The cleanup instruction for a variant."""
    return ac.REFACTOR_TICKET if variant["scope"] == "all" else CLEANUP_TARGETED.format(files="\n".join(f"- {t}" for t in targets))


def record(stage: dict, label: str, kind: str) -> dict:
    """Name a stage record."""
    return {**stage, "label": label, "kind": kind}


def run_branch(name: str, base: ac.Workspace, rep: int, client, args, budget: Budget, rec: dict) -> None:
    """One branch from the shared state: optional cleanup, then the remaining tickets."""
    variant = VARIANTS.get(name)
    workspace = branch_from(base, args.workspaces / f"ladder{rep}_{name}", variant["level"] if variant else "standard")
    stages = rec["branches"].setdefault(name, {"stages": []})["stages"]
    if variant:
        targets = hot_files(args.history_repo, "src", workspace, args.hot_top) if variant["scope"] == "hot" else []
        stage = ac.run_stage(client, workspace, f"{rep}/{name}/cleanup", cleanup_ticket(variant, targets), CLEANUP_SYSTEM, ac.TOOLS + [ac.KRYPTON_TOOL], 70, budget, args.hidden)
        stages.append(record(stage, "cleanup", "cleanup") | {"targets": targets})
        finish(args, rec, budget, f"{rep} {name:<8} cleanup", stage)
    for number in args.tasks[1:]:
        stage = ac.run_stage(client, workspace, f"{rep}/{name}/ticket {number}", ac.TICKETS[number], ac.BASE_PROMPT, ac.TOOLS, 45, budget, args.hidden)
        stages.append(record(stage, f"ticket {number}", "ticket"))
        finish(args, rec, budget, f"{rep} {name:<8} ticket {number}", stage)


def finish(args, rec: dict, budget: Budget, label: str, stage: dict) -> None:
    """Save, print one line, and stop if the budget guard fired."""
    save(args, rec, budget)
    totals = stage["totals"]
    print(f"  {label:<20} {totals['requests']:>3} requests  ${totals['cost']:.3f}  cached {totals['cached_share']:.0%}  stop={stage['stop']}  total ${budget.spent:.2f}", flush=True)
    if stage["stop"].startswith("budget"):
        raise BudgetExceeded(stage["stop"])


def run_rep(rep: int, client, args, budget: Budget, results: dict) -> None:
    """Ticket 1 once on the regular code, then every branch from that state."""
    base = ac.fresh_workspace(args.source, args.workspaces / f"ladder{rep}_base", args.python)
    rec = results.setdefault(str(rep), {"rep": rep, "start": ac.code_stats(base), "branches": {}})
    first = ac.run_stage(client, base, f"{rep}/ticket {args.tasks[0]}", ac.TICKETS[args.tasks[0]], ac.BASE_PROMPT, ac.TOOLS, 45, budget, args.hidden)
    rec["shared"] = record(first, f"ticket {args.tasks[0]}", "ticket")
    finish(args, rec, budget, f"{rep} shared ticket {args.tasks[0]}", first)
    for name in ("control", *args.variants):
        run_branch(name, base, rep, client, args, budget, rec)


def save(args, rec: dict, budget: Budget) -> None:
    """Write everything so far to the results file (no key, no prompts, no file contents)."""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(args.out.read_text(encoding="utf-8"))["reps"] if args.out.exists() else {}
    existing[str(rec["rep"])] = rec
    meta = {"date": date.today().isoformat(), "model": MODEL, "prices_per_mtok": PRICES, "spent_usd": round(budget.spent, 6), "max_usd": budget.cap, "tickets": ac.TICKETS,
            "variants": {name: VARIANTS[name] for name in args.variants}, "tasks": args.tasks}
    args.out.write_text(json.dumps({"meta": meta, "reps": existing}, indent=1), encoding="utf-8")


def estimate(tasks: list[int], variants: list[str]) -> float:
    """A rough cost per repetition, from the earlier measurements."""
    tickets = PER_TICKET * (len(tasks) - 1) * (1 + len(variants)) + 0.065
    return tickets + PER_CLEANUP * len(variants)


def main(args: argparse.Namespace) -> int:
    """Plan, then run the repetitions."""
    print(f"tickets {args.tasks} (the first is shared), branches: control + {', '.join(args.variants)}; model {MODEL}; budget ${args.max_usd:.2f}")
    print(f"about ${estimate(args.tasks, args.variants):.2f} per repetition from earlier measurements, so about ${estimate(args.tasks, args.variants) * args.reps:.2f} for {args.reps}")
    if args.dry:
        workspace = ac.fresh_workspace(args.source, args.workspaces / "ladder_dry", args.python)
        print("files the targeted cleanup would take (from git history only):", ", ".join(hot_files(args.history_repo, "src", workspace, args.hot_top)))
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
            run_rep(rep, client, args, budget, results)
    except BudgetExceeded as stop:
        print(f"stopped by the budget guard: {stop}")
    print(f"\ntotal spend ${budget.spent:.4f} of ${args.max_usd:.2f}; saved to {args.out}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measure whether iterating on Krypton-cleaned code is cheaper for an agent.")
    parser.add_argument("--tasks", default="123456", help="ticket numbers in order, like 123456 or 2,7,8,9,10,11; the first is shared by every branch. 2,7,8,9,10,11 iterates one feature (the Body Battery event query) six times")
    parser.add_argument("--variants", default="standard,targeted", help="comma list from: " + ", ".join(VARIANTS))
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--rep-start", type=int, default=0)
    parser.add_argument("--max-usd", type=float, default=20.0)
    parser.add_argument("--hot-top", type=int, default=6, help="how many frequently changed, flagged files the targeted cleanup takes")
    parser.add_argument("--source", type=Path, required=True, help="a clean copy of garth (with tests)")
    parser.add_argument("--history-repo", type=Path, required=True, help="garth's git repository, for the change history")
    parser.add_argument("--hidden", type=Path, default=Path(__file__).parent / "garth_acceptance" / "hidden_tests.py")
    parser.add_argument("--python", type=Path, required=True, help="a Python with garth's test dependencies installed")
    parser.add_argument("--workspaces", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ac.ROOT / "cache_anthropic_results" / f"ladder_{date.today().isoformat()}.json")
    parser.add_argument("--env-root", type=Path, default=ac.ROOT)
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--yes", action="store_true")
    options = parser.parse_args()
    options.tasks = parse_tasks(options.tasks)
    options.variants = [v for v in options.variants.split(",") if v]
    unknown = [v for v in options.variants if v not in VARIANTS]
    if unknown:
        parser.error(f"unknown variants: {unknown}")
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(options))
