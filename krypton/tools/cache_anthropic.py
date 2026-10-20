"""Does Krypton's volatility order help real Anthropic prompt caching? Replay a repo's real commits and keep the raw usage JSON.

    python -m tools.cache_anthropic REPO --dry                          # plan and predictions, no API calls
    python -m tools.cache_anthropic REPO --max-usd 20 --yes             # live run, stops before the budget is passed

Same replay as tools.cache_live (turn 0 = the source tree at a base commit, turn N = it after the Nth later commit that
touched it; the file order is fixed from history BEFORE the replay), but sent to Claude with explicit cache breakpoints,
because Anthropic only reads a cache entry at a position where an earlier request wrote one. Two strategies:

  quartiles  breakpoints after the files that end the first 25%, 50% and 75% of the code at turn 0, plus one at the end
  end        a single breakpoint at the end (the control: it only hits when nothing at all changed; run once per order)

Every run carries a unique line at the top of the system prompt, so no run can read another run's cache. The key is read
from the environment or the repository's .env (ANTHROPIC_API_KEY, ANTHROPIC_KEY or TEST_KEY, any capitalisation) and
never printed. Every request's full `usage` object is saved, and the run stops before spending more than --max-usd.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from krypton.engine.config import EngineConfig
from tools import cache_live
from tools.cache_live import base_counts, commits, orders, place, tree

MODEL = "claude-sonnet-5-5"
PRICES = {"input": 2.00, "output": 10.00, "read": 0.20, "write": 2.50}  # USD per million tokens (5-minute cache writes)
KEY_NAMES = ("anthropic_api_key", "anthropic_key", "test_key")
STRATEGIES = ("quartiles", "end")
QUESTION = "\n\nReply with the single word OK."
MAX_TOKENS = 24
CHARS_PER_TOKEN = 2.4  # deliberately low (Claude's tokenizer measured about 2.6 on this code), so cost estimates err high
LOOKBACK = 20  # a breakpoint can find an earlier entry up to this many blocks back


class BudgetExceeded(Exception):
    """The next request could push spending past the cap."""


@dataclass
class Budget:
    """Running spend against a hard cap."""

    cap: float
    spent: float = 0.0

    def check(self, worst_case: float) -> None:
        """Raise BudgetExceeded unless spending this much more still fits under the cap."""
        if self.spent + worst_case > self.cap:
            raise BudgetExceeded(f"spent ${self.spent:.2f}, next request could cost ${worst_case:.2f}, cap ${self.cap:.2f}")


def find_key(root: Path, env=os.environ) -> str | None:
    """The API key from the environment or `root/.env`; None if there is none. Never printed."""
    for name in KEY_NAMES:
        for key, value in env.items():
            if key.lower() == name and value.strip():
                return value.strip()
    path = root / ".env"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        name, eq, value = line.partition("=")
        if eq and name.strip().lower() in KEY_NAMES and value.strip():
            return value.strip().strip('"').strip("'")
    return None


def request_cost(usage: dict) -> float:
    """Dollar cost of one reply from its usage object."""
    return sum(usage.get(field, 0) * PRICES[rate] for field, rate in
               (("input_tokens", "input"), ("output_tokens", "output"), ("cache_read_input_tokens", "read"), ("cache_creation_input_tokens", "write"))) / 1e6


def worst_case(chars: int) -> float:
    """The most one request could cost: every prompt token written to the cache, plus the longest reply."""
    return (chars / CHARS_PER_TOKEN * PRICES["write"] + MAX_TOKENS * PRICES["output"]) / 1e6


def anchors(files: dict[str, str], order: list[str]) -> list[str]:
    """The files that end the first 25%, 50% and 75% of the code at turn 0, in `order` (breakpoints stay on these names)."""
    names = place(files, order)
    total, running, found = sum(len(files[n]) for n in names), 0, []
    for name in names:
        running += len(files[name])
        if len(found) < 3 and running >= total * (len(found) + 1) / 4:
            found.append(name)
    return found


def blocks_for(files: dict[str, str], order: list[str], marks: list[str]) -> list[dict]:
    """One text block per file in `order`, then the question; blocks named in `marks` get a cache breakpoint."""
    names = place(files, order)
    out = [{"type": "text", "text": f"=== {n} ===\n{files[n]}\n"} for n in names]
    for index, name in enumerate(names):
        if name in marks:
            out[index]["cache_control"] = {"type": "ephemeral"}
    return out + [{"type": "text", "text": QUESTION}]


def breakpoints(strategy: str, files: dict[str, str], order: list[str], fixed: list[str]) -> list[str]:
    """Which files carry a breakpoint on this turn: the fixed anchors that still exist, plus the last file."""
    names = place(files, order)
    last = [names[-1]]
    return last if strategy == "end" else [n for n in fixed if n in files] + last


def prefix_hashes(blocks: list[dict]) -> list[str]:
    """A hash of the text up to and including each block (the cache key a breakpoint there would use)."""
    digest, out = hashlib.sha1(), []
    for block in blocks:
        digest.update(block["text"].encode("utf-8"))
        out.append(digest.copy().hexdigest())
    return out


def predicted_read(blocks: list[dict], written: set[str]) -> float:
    """Share of the prompt a breakpoint should read: the longest earlier-written prefix within the lookback of any breakpoint."""
    hashes, sizes = prefix_hashes(blocks), [len(b["text"]) for b in blocks]
    marked = [i for i, b in enumerate(blocks) if "cache_control" in b]
    hits = [j for i in marked for j in range(i, max(-1, i - LOOKBACK), -1) if hashes[j] in written]
    return sum(sizes[: max(hits) + 1]) / sum(sizes) if hits else 0.0


def ask(client, blocks: list[dict], nonce: str) -> tuple[dict, str, float]:
    """One request; (usage dict, reply text, seconds). Thinking is off so the reply cost is a few tokens."""
    started = time.monotonic()
    reply = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, thinking={"type": "between_tools"},
        system=[{"type": "text", "text": f"run {nonce}"}], messages=[{"role": "user", "content": blocks}],
    )
    text = "".join(getattr(b, "text", "") for b in reply.content)
    return reply.usage.model_dump(), text, time.monotonic() - started


def run_one(client, states, order_name, order, strategy, rep, budget, pause) -> dict:
    """Replay every turn for one (order, strategy, repetition); returns the run with every raw usage object."""
    nonce, fixed, written, turns = uuid.uuid4().hex[:10], anchors(states[0], order), set(), []
    run = {"order": order_name, "strategy": strategy, "rep": rep, "nonce": nonce, "anchors": fixed, "turns": turns, "complete": True}
    for turn, files in enumerate(states):
        marks = breakpoints(strategy, files, order, fixed)
        blocks = blocks_for(files, order, marks)
        row = {"turn": turn, "chars": sum(len(b["text"]) for b in blocks), "breakpoints": marks, "predicted_read": predicted_read(blocks, written)}
        try:
            budget.check(worst_case(row["chars"]))
        except BudgetExceeded as stop:
            run["complete"], run["stopped"] = False, str(stop)
            return run
        if client:
            row["usage"], row["reply"], row["seconds"] = ask(client, blocks, nonce)
            row["cost"] = request_cost(row["usage"])
            budget.spent += row["cost"]
            time.sleep(pause)
        written.update(prefix_hashes(blocks)[i] for i, b in enumerate(blocks) if "cache_control" in b)
        turns.append(row)
    return run


def measured_share(run: dict) -> float | None:
    """Cached share of prompt tokens over the replay turns (turn 0 is a cold start and excluded)."""
    rows = [t["usage"] for t in run["turns"][1:] if "usage" in t]
    total = sum(u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"] for u in rows)
    return sum(u["cache_read_input_tokens"] for u in rows) / total if total else None


def summarize(runs: list[dict]) -> None:
    """One line per run: predicted and measured cached share over the replay turns, and what the run cost."""
    print("\n order         strategy   rep  predicted  measured   cost")
    for run in runs:
        share = measured_share(run)
        predicted = sum(t["predicted_read"] for t in run["turns"][1:]) / max(1, len(run["turns"]) - 1)
        cost = sum(t.get("cost", 0) for t in run["turns"])
        flag = "" if run["complete"] else "  (stopped early)"
        print(f" {run['order']:<13} {run['strategy']:<10} {run['rep']:>3}  {predicted:>8.0%}  {('%.0f%%' % (share * 100)) if share is not None else '-':>8}  ${cost:.4f}{flag}")


def build_plan(args, scratch: Path):
    """The replay states and the three fixed file orders, from git history."""
    repo = args.repo.resolve()
    chain = commits(repo, args.subdir, args.turns)
    states = [tree(repo, commit, args.subdir) for commit in chain]
    counts = base_counts(repo, chain[0], args.subdir, EngineConfig().volatility_window_commits, scratch)
    return chain, states, orders(set(states[0]), counts)


def main(args: argparse.Namespace) -> int:
    """Plan, optionally run, save the raw results, print the table."""
    cache_live.SUFFIXES = tuple(s.strip() for s in args.ext.split(","))
    scratch = args.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    chain, states, plan = build_plan(args, scratch)
    chars = sum(len(t) for t in states[0].values())
    configs = [(o, s, r) for r in range(args.reps) for o in plan for s in STRATEGIES if s == "quartiles" or (r == 0 and not args.no_control)]  # the one-breakpoint control runs once, unless skipped
    estimate = len(configs) * len(states) * worst_case(chars)
    print(f"{len(states) - 1} replay commits; {len(states[0])} files, about {chars / 3.5:,.0f} tokens per prompt; {len(configs)} runs x {len(states)} turns = {len(configs) * len(states)} requests")
    print(f"worst case if nothing were ever cached: ${estimate:.2f}; budget cap ${args.max_usd:.2f}")
    client = None
    if not args.dry:
        key = find_key(args.env_root)
        if not key:
            print("no Anthropic key found (ANTHROPIC_API_KEY, ANTHROPIC_KEY or TEST_KEY in the environment or .env)", file=sys.stderr)
            return 2
        if not args.yes:
            print("re-run with --yes to spend real money", file=sys.stderr)
            return 2
        import anthropic

        client = anthropic.Anthropic(api_key=key, max_retries=6)
    budget, runs = Budget(args.max_usd), []
    for order_name, strategy, rep in configs:
        run = run_one(client, states, order_name, plan[order_name], strategy, rep, budget, args.pause)
        runs.append(run)
        print(f"  {order_name:<13} {strategy:<9} rep {rep}: {'done' if run['complete'] else 'STOPPED'}  total ${budget.spent:.3f}", flush=True)
        save(args, chain, plan, runs, budget)
        if not run["complete"]:
            break
    summarize(runs)
    print(f"\ntotal spend ${budget.spent:.4f} of ${args.max_usd:.2f}; raw usage saved to {args.out}")
    return 0


def save(args, chain, plan, runs, budget) -> None:
    """Write everything so far, raw usage included (no key, no prompt text)."""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"date": date.today().isoformat(), "model": MODEL, "prices_per_mtok": PRICES, "repo": args.repo.name, "subdir": args.subdir, "commits": [c[:8] for c in chain],
            "orders": {k: v for k, v in plan.items()}, "max_usd": args.max_usd, "spent_usd": round(budget.spent, 6), "reps": args.reps}
    args.out.write_text(json.dumps({"meta": meta, "runs": runs}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay real commits against Claude to measure prompt-cache hits per file order.")
    parser.add_argument("repo", type=Path, help="a git repository with history")
    parser.add_argument("--subdir", default="src")
    parser.add_argument("--ext", default=".py", help="comma-separated file suffixes to send, for example .ts or .ts,.js")
    parser.add_argument("--turns", type=int, default=12, help="later commits to replay")
    parser.add_argument("--reps", type=int, default=2, help="repetitions of every order and strategy")
    parser.add_argument("--max-usd", type=float, default=20.0, help="hard cap on spend")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between requests")
    parser.add_argument("--scratch", type=Path, default=Path("cache_anthropic_scratch"))
    parser.add_argument("--out", type=Path, default=Path("cache_anthropic_results") / f"garth_{date.today().isoformat()}.json")
    parser.add_argument("--env-root", type=Path, default=Path(__file__).resolve().parents[2], help="folder holding the .env file")
    parser.add_argument("--no-control", action="store_true", help="skip the one-breakpoint control runs (already measured on another repository)")
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--yes", action="store_true")
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(parser.parse_args()))
