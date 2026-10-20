"""Does Krypton's volatility order help real prompt caching? Replay a repo's real commits against a live model.

    python -m tools.cache_live REPO_WITH_GIT_HISTORY --dry                  # no API: predicted cache hits only
    python -m tools.cache_live REPO --turns 12 --model openai/gpt-4.1-nano --yes

The file order is fixed from history BEFORE the replay window (Krypton's own change_frequency / order_by_volatility
on the commits up to the base commit), so the test asks: do files that changed often before keep changing
afterwards? Turn 0 sends the whole source tree at the base commit; turn N sends it as of the Nth later commit that
touched the source. Three orders are compared: volatility (stable first), alphabetical, reverse volatility.
Each turn asks the model for one word; OpenRouter reports `cached_tokens` (OpenAI caches the longest repeated prefix
automatically, in 128-token steps, from 1024 tokens). A prediction from the shared prefix is printed next to it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from pathlib import Path

import tiktoken
from openai import APIStatusError

from krypton.engine.config import EngineConfig
from krypton.engine.volatility import change_frequency, order_by_volatility
from krypton.history import commit_counts
from tools.openrouter_bench import make_client

ENCODING = tiktoken.get_encoding("o200k_base")
QUESTION = "\n\nReply with the single word OK."


def git(repo: Path, *args: str) -> str:
    """Text output of a git command in `repo`."""
    done = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args], capture_output=True, check=True)
    return done.stdout.decode("utf-8", errors="replace")


def commits(repo: Path, subdir: str, turns: int) -> list[str]:
    """The base commit followed by the next `turns` commits (oldest first) that touched `subdir`."""
    newest_first = git(repo, "rev-list", "--first-parent", f"-n{turns + 1}", "HEAD", "--", subdir).split()
    return newest_first[::-1]


SUFFIXES = (".py",)   # which files are sent; cache_anthropic's --ext replaces it (for example ".ts")


def tree(repo: Path, commit: str, subdir: str) -> dict[str, str]:
    """{path relative to subdir: text} of every file ending in one of SUFFIXES under `subdir` at `commit`."""
    names = [n for n in git(repo, "ls-tree", "-r", "--name-only", commit, "--", subdir).splitlines() if n.endswith(SUFFIXES)]
    return {n[len(subdir) + 1:]: git(repo, "show", f"{commit}:{n}") for n in names}


def base_counts(repo: Path, base: str, subdir: str, window: int, scratch: Path) -> dict[str, int]:
    """Krypton's own commit_counts, run in a throwaway clone checked out at the base commit (history before the replay only)."""
    clone = scratch / f"base_{base[:8]}"
    if not clone.exists():
        subprocess.run(["git", "clone", "--quiet", str(repo), str(clone)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "--quiet", base], check=True, capture_output=True)
    return dict(commit_counts(clone / subdir, window))


def orders(names: set[str], counts: dict[str, int]) -> dict[str, list[str]]:
    """The three file orders, fixed from the base commit's files; files that appear later count as in flux."""
    frequency = change_frequency(counts, sorted(names))
    stable_first = order_by_volatility(frequency)
    return {"volatility": stable_first, "alphabetical": sorted(names), "reverse": stable_first[::-1]}


def place(files: dict[str, str], order: list[str]) -> list[str]:
    """Order for one turn: known files in `order`, brand-new files last (in flux = most volatile)."""
    return [n for n in order if n in files] + sorted(set(files) - set(order))


def prompt(files: dict[str, str], order: list[str], nonce: str) -> str:
    """One prompt: a unique run line, every file under a header in `order`, then the question."""
    body = "".join(f"=== {name} ===\n{files[name]}\n" for name in place(files, order))
    return f"run {nonce}\n{body}{QUESTION}"


def shared_prefix(a: list[int], b: list[int]) -> int:
    """Length of the common token prefix of two token lists."""
    count = 0
    for x, y in zip(a, b):
        if x != y:
            break
        count += 1
    return count


def predicted_cached(previous: list[int] | None, current: list[int]) -> int:
    """What OpenAI-style automatic caching should serve: the shared prefix rounded down to 128, if at least 1024."""
    if previous is None:
        return 0
    prefix = shared_prefix(previous, current)
    return prefix // 128 * 128 if prefix >= 1024 else 0


def ask(client, model: str, text: str):
    """One one-word-answer request; a 402 'in-flight budget' (a temporary new-account cap) is waited out and retried."""
    for attempt in range(3):
        try:
            return client.chat.completions.create(model=model, messages=[{"role": "user", "content": text}], max_tokens=16, temperature=0, extra_body={"usage": {"include": True}})
        except APIStatusError as error:
            if error.status_code != 402 or attempt == 2:
                raise
            time.sleep(125)


def run_order(name: str, order: list[str], states: list[dict[str, str]], args, client) -> list[dict]:
    """Send every turn for one order; returns per-turn rows."""
    nonce, rows, previous = uuid.uuid4().hex[:10], [], None
    for turn, files in enumerate(states):
        text = prompt(files, order, nonce)
        tokens = ENCODING.encode(text)
        row = {"order": name, "turn": turn, "prompt_est": len(tokens), "predicted": predicted_cached(previous, tokens), "prompt": 0, "cached": 0, "cost": 0.0}
        if client:
            reply = ask(client, args.model, text)
            usage = reply.usage.model_dump()
            row.update(prompt=usage.get("prompt_tokens", 0), cached=(usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0, cost=usage.get("cost") or 0.0)
            time.sleep(args.pause)  # give the provider a moment to register the cache entry
        rows.append(row)
        previous = tokens
    return rows


def show(rows: list[dict], changed: list[int], live: bool) -> None:
    """One table per order plus its totals."""
    for name in dict.fromkeys(r["order"] for r in rows):
        mine = [r for r in rows if r["order"] == name]
        print(f"\n[{name}]\n turn files changed  prompt   predicted   {'actual cached' if live else ''}      cost")
        for r in mine:
            size = r["prompt"] or r["prompt_est"]
            actual = f"{r['cached']:>8,} ({r['cached'] / size:>4.0%})" if live else ""
            print(f" {r['turn']:>4} {changed[r['turn']]:>13} {size:>7,} {r['predicted']:>9,} ({r['predicted'] / size:>4.0%})  {actual:<17} ${r['cost']:.5f}")
        total = sum(r["prompt"] or r["prompt_est"] for r in mine[1:])
        print(f" replay turns: predicted {sum(r['predicted'] for r in mine[1:]) / total:.0%} of prompt tokens cached"
              + (f", actual {sum(r['cached'] for r in mine[1:]) / total:.0%}, spend ${sum(r['cost'] for r in mine):.4f}" if live else ""))


def main(args: argparse.Namespace) -> int:
    """Build the turns from git history, run (or predict) the three orders, print the tables."""
    repo, scratch = args.repo.resolve(), args.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    chain = commits(repo, args.subdir, args.turns)
    states = [tree(repo, commit, args.subdir) for commit in chain]
    changed = [0] + [sum(states[i].get(n) != text for n, text in states[i + 1].items()) + len(set(states[i]) - set(states[i + 1])) for i in range(len(states) - 1)]
    counts = base_counts(repo, chain[0], args.subdir, EngineConfig().volatility_window_commits, scratch)
    plan = orders(set(states[0]), counts)
    print(f"{len(chain) - 1} replay commits after base {chain[0][:8]}; {len(states[0])} files, ~{len(ENCODING.encode(prompt(states[0], plan['volatility'], 'x'))):,} tokens per prompt")
    print("most volatile before the replay:", ", ".join(plan["volatility"][-4:][::-1]))
    client = None
    if not args.dry:
        if not (args.yes or args.model.endswith(":free")):
            print(f"about {3 * (args.turns + 1)} requests of ~25K tokens on {args.model}; re-run with --yes", file=sys.stderr)
            return 2
        client = make_client()
    rows = [row for name, order in plan.items() for row in run_order(name, order, states, args, client)]
    show(rows, changed, bool(client))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay real commits against a live model to measure prompt-cache hits per file order.")
    parser.add_argument("repo", type=Path, help="a git repository with history")
    parser.add_argument("--subdir", default="src", help="folder inside the repo to send (default src)")
    parser.add_argument("--turns", type=int, default=12, help="later commits to replay")
    parser.add_argument("--model", default="openai/gpt-4.1-nano")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between calls")
    parser.add_argument("--scratch", type=Path, default=Path("cache_live_scratch"))
    parser.add_argument("--dry", action="store_true", help="no API calls; predictions only")
    parser.add_argument("--yes", action="store_true")
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(parser.parse_args()))
