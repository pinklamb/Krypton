"""Prompt-cache replay: what does Argon's --order-by-volatility save when a file changes?

    python -m tools.cache_replay argon --mock                    # free, simulated cache (checks the harness)
    python -m tools.cache_replay argon --max-usd 3 --yes         # real API, needs ANTHROPIC_API_KEY

The same 8-turn script runs against three file orders: Argon's volatility order (stable first),
alphabetical, and the reverse (volatile first, the adversarial control). Each turn sends the
whole folder as one prompt in three cache-marked blocks, split at the same character fractions
for every order. Between turns one scripted edit lands on a volatile, middle or stable file.
Every turn the API's own `usage` object gives input_tokens, cache_creation_input_tokens and
cache_read_input_tokens; dollars come from those, never from an estimate.

--mock replays the same logic against a simulated cache with chars/3.5 token estimates. Its
numbers show the harness works and the expected shape; they are NOT measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
CHARS_PER_TOKEN = 3.5
BREAK_FRACTIONS = (0.5, 0.8, 1.0)  # cache breakpoints at these shares of the prompt, same for every order
SYSTEM = "You are reviewing a Python code base. Answer in one word."
QUESTION = "Reply with the single word OK."
CACHE_WRITE_FACTOR = 1.25  # 5-minute TTL write premium over the input price
SCHEDULE = [
    ("cold start", []),
    ("no change", []),
    ("edit volatile", ["volatile"]),
    ("edit middle", ["middle"]),
    ("edit stable", ["stable"]),
    ("edit volatile again", ["volatile"]),
    ("edit 2 volatile", ["volatile", "volatile2"]),
    ("no change", []),
]


@dataclass(frozen=True)
class Price:
    """USD per million tokens; cache writes are CACHE_WRITE_FACTOR x input."""

    input: float
    output: float
    read: float


# Anthropic list prices as cached in the claude-api skill on 2026-09-25; re-check before quoting.
PRICES = {
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20),
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
}


@dataclass
class Turn:
    """One request's exact usage and what it cost, against what no caching would have cost."""

    order: str
    turn: int
    label: str
    input_tokens: int
    cache_write: int
    cache_read: int
    output_tokens: int
    usd: float
    uncached_usd: float


class CapExceeded(RuntimeError):
    """The running cost passed --max-usd; the experiment stops rather than overspend."""


def turn_cost(usage, price: Price) -> tuple[float, float]:
    """(dollars paid, dollars an uncached prompt of the same size would have cost) for one usage object."""
    prompt = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
    paid = (
        usage.input_tokens * price.input
        + usage.cache_creation_input_tokens * price.input * CACHE_WRITE_FACTOR
        + usage.cache_read_input_tokens * price.read
        + usage.output_tokens * price.output
    ) / 1e6
    return paid, (prompt * price.input + usage.output_tokens * price.output) / 1e6


def volatility_order(folder: Path) -> list[str]:
    """File names, most stable first, from the real `argon.cli --order-by-volatility` manifest."""
    done = subprocess.run([sys.executable, "-m", "argon.cli", str(folder), "--order-by-volatility"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [entry["filename"] for entry in json.loads(done.stdout)["files"]]


def targets(order: list[str]) -> dict[str, str]:
    """The files the scripted edits hit, picked by Argon's rank so every prompt order edits the same ones."""
    return {"stable": order[0], "middle": order[len(order) // 2], "volatile": order[-1], "volatile2": order[-2]}


def render(path: str, text: str, edits: int) -> str:
    """One file as it appears in the prompt; each edit appends a comment line."""
    return f"=== {path} ===\n{text}\n" + "# edit\n" * edits


def cut_indices(order: list[str], corpus: dict[str, str]) -> list[int]:
    """File indices that close each cache block, fixed from the unedited corpus so edits never move a boundary."""
    sizes = [len(render(name, corpus[name], 0)) for name in order]
    total, seen, cuts = sum(sizes), 0, []
    for index, size in enumerate(sizes):
        seen += size
        if len(cuts) < len(BREAK_FRACTIONS) - 1 and seen >= total * BREAK_FRACTIONS[len(cuts)]:
            cuts.append(index + 1)
    return cuts


def build_blocks(order: list[str], corpus: dict[str, str], edits: dict[str, int], cuts: list[int]) -> list[str]:
    """The cacheable text blocks: files in `order`, sliced at `cuts`."""
    pieces = [render(name, corpus[name], edits.get(name, 0)) for name in order]
    bounds = [0, *cuts, len(pieces)]
    return ["".join(pieces[start:end]) for start, end in zip(bounds, bounds[1:], strict=False) if start < end]


def build_request(model: str, nonce: str, blocks: list[str]) -> dict:
    """The Messages request: a nonce in the system prompt isolates this run from earlier cache entries."""
    marked = [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}} for text in blocks]
    return {
        "model": model,
        "max_tokens": 200,
        "output_config": {"effort": "low"},
        "system": f"{SYSTEM} run={nonce}",
        "messages": [{"role": "user", "content": [*marked, {"type": "text", "text": QUESTION}]}],
    }


class MockClient:
    """A stand-in for anthropic.Anthropic that applies prefix-cache rules to chars/3.5 token estimates."""

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **request) -> SimpleNamespace:
        """Return a response whose usage follows the cache rules: read the longest known breakpoint prefix."""
        prefix, points, total = request["system"], [], 0
        for block in request["messages"][0]["content"]:
            prefix += block["text"]
            total += len(block["text"])
            if "cache_control" in block:
                points.append((hashlib.sha256(prefix.encode()).hexdigest(), total))
        total += len(request["system"])
        read = max((size for key, size in points if key in self.seen), default=0)
        last = points[-1][1] if points else 0
        self.seen.update(dict(points))
        tokens = lambda chars: round(chars / CHARS_PER_TOKEN)  # noqa: E731
        usage = SimpleNamespace(
            input_tokens=tokens(total - last), cache_creation_input_tokens=tokens(last - read), cache_read_input_tokens=tokens(read), output_tokens=2
        )
        return SimpleNamespace(usage=usage)


class Ledger:
    """Running dollars spent; raises CapExceeded the moment the cap is passed."""

    def __init__(self, cap: float) -> None:
        self.cap, self.spent = cap, 0.0

    def add(self, usd: float) -> None:
        """Record spend, then enforce the cap."""
        self.spent += usd
        if self.spent > self.cap:
            raise CapExceeded(f"spent ${self.spent:.3f}, over the ${self.cap:.2f} cap")


def run_order(client, model: str, name: str, order: list[str], corpus: dict[str, str], edit_files: dict[str, str], ledger: Ledger) -> list[Turn]:
    """Run the whole SCHEDULE for one file order and return each turn's measured usage."""
    price, nonce = PRICES[model], uuid.uuid4().hex[:12]
    cuts, edits, turns = cut_indices(order, corpus), {}, []
    for number, (label, hits) in enumerate(SCHEDULE, start=1):
        for role in hits:
            edits[edit_files[role]] = edits.get(edit_files[role], 0) + 1
        usage = client.messages.create(**build_request(model, nonce, build_blocks(order, corpus, edits, cuts))).usage
        paid, uncached = turn_cost(usage, price)
        ledger.add(paid)
        turns.append(Turn(name, number, label, usage.input_tokens, usage.cache_creation_input_tokens, usage.cache_read_input_tokens, usage.output_tokens, paid, uncached))
    return turns


def read_corpus(folder: Path) -> dict[str, str]:
    """Every .py file under folder keyed by its posix path relative to folder."""
    return {path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8") for path in sorted(folder.rglob("*.py")) if "__pycache__" not in path.parts}


def orderings(stable_first: list[str]) -> dict[str, list[str]]:
    """The three prompt orders compared."""
    return {"volatility": stable_first, "alphabetical": sorted(stable_first), "reverse": stable_first[::-1]}


def table(turns: list[Turn]) -> list[str]:
    """Text rows for one order's turns plus its total."""
    rows = [f"{'#':>2} {'turn':20}{'uncached':>10}{'cache write':>13}{'cache read':>12}{'out':>5}{'USD':>10}"]
    rows += [f"{t.turn:>2} {t.label:20}{t.input_tokens:>10,}{t.cache_write:>13,}{t.cache_read:>12,}{t.output_tokens:>5}{t.usd:>10.4f}" for t in turns]
    paid, uncached = sum(t.usd for t in turns), sum(t.uncached_usd for t in turns)
    rows.append(f"   total ${paid:.4f} vs ${uncached:.4f} with no caching ({1 - paid / uncached:.0%} saved)")
    return rows


def preflight(corpus: dict[str, str], model: str, count: int) -> float:
    """Worst-case dollars if every turn of every order were a full cache miss (chars/3.5 estimate)."""
    tokens = sum(len(text) for text in corpus.values()) / CHARS_PER_TOKEN
    return tokens * PRICES[model].input * CACHE_WRITE_FACTOR / 1e6 * len(SCHEDULE) * count


def main(argv: list[str] | None = None) -> int:
    """Run the replay for every order and print one table each."""
    parser = argparse.ArgumentParser(description="Prompt-cache replay for Argon's volatility ordering.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--model", default="claude-sonnet-5-5", choices=sorted(PRICES))
    parser.add_argument("--mock", action="store_true", help="simulate the cache offline; numbers are NOT measurements")
    parser.add_argument("--max-usd", type=float, default=3.0, help="stop if the running cost passes this")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation (real API only)")
    parser.add_argument("--out", type=Path, help="write every turn's numbers as JSON")
    args = parser.parse_args(argv)
    corpus = read_corpus(args.folder)
    stable_first = volatility_order(args.folder)
    plans = orderings(stable_first)
    worst = preflight(corpus, args.model, len(plans))
    print(f"{len(corpus)} files, worst case ${worst:.2f} (every turn a full miss), cap ${args.max_usd:.2f}, model {args.model}")
    if not args.mock and not args.yes:
        print("real API calls will be billed to your API key; re-run with --yes to proceed", file=sys.stderr)
        return 2
    if args.mock:
        client = MockClient()
    else:
        import anthropic

        client = anthropic.Anthropic()
    ledger, edit_files, results = Ledger(args.max_usd), targets(stable_first), []
    for name, order in plans.items():
        turns = run_order(client, args.model, name, order, corpus, edit_files, ledger)
        results += turns
        print(f"\n[{name}]" + ("  SIMULATED" if args.mock else ""), *table(turns), sep="\n")
    if args.out:
        args.out.write_text(json.dumps({"mock": args.mock, "model": args.model, "edit_files": edit_files, "turns": [asdict(t) for t in results]}, indent=2), encoding="utf-8")
    print(f"\nspent ${ledger.spent:.4f}" + (" (simulated)" if args.mock else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
