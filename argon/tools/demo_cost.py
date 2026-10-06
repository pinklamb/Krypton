"""Demo: what does a codebase cost an LLM agent to maintain, before and after an Argon-guided refactor?

    python -m tools.demo_cost <before folder> [<after folder>] [--sessions 100]

Argon scores each folder (same engine and CLI as `python -m argon.cli`). Two costs per model:

  read    every agent session loads all scanned source as input:  tokens x input price x sessions
  hotspot every flagged file is read and rewritten once:          tokens x (input + output price)

Tokens are estimated as characters / 3.5 for every model (no vendor tokenizer is bundled), so
the dollar figures compare folders and models with each other; they are not a billing quote.
Prices live in tools/model_prices.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from argon.schemas import EngineResult

ROOT = Path(__file__).resolve().parent.parent
CHARS_PER_TOKEN = 3.5


@dataclass(frozen=True)
class Scan:
    """One folder's Argon result plus the token counts the cost model needs."""

    folder: Path
    result: EngineResult
    tokens: int
    flagged_tokens: int


def estimate_tokens(path: Path) -> int:
    """Estimated tokens in a source file: characters / CHARS_PER_TOKEN."""
    return round(len(path.read_text(encoding="utf-8", errors="replace")) / CHARS_PER_TOKEN)


def scan(folder: Path) -> Scan:
    """Run the Argon CLI on a folder (JSON mode) and total the tokens of scanned and flagged files."""
    done = subprocess.run([sys.executable, "-m", "argon.cli", str(folder), "--json"], cwd=ROOT, capture_output=True, text=True, check=True)
    result = EngineResult.model_validate_json(done.stdout)
    sizes = {finding.filename: estimate_tokens(folder / finding.filename) for finding in result.findings}
    flagged = sum(sizes[finding.filename] for finding in result.findings if finding.flagged)
    return Scan(folder, result, sum(sizes.values()), flagged)


def load_prices() -> list[dict]:
    """The model price table (USD per million tokens)."""
    return json.loads((ROOT / "tools" / "model_prices.json").read_text(encoding="utf-8"))["models"]


def costs(scanned: Scan, model: dict, sessions: int) -> tuple[float, float]:
    """(read cost over `sessions`, one-off hotspot rewrite cost) in dollars."""
    read = scanned.tokens * model["input"] / 1e6 * sessions
    hotspot = scanned.flagged_tokens * (model["input"] + model["output"]) / 1e6
    return read, hotspot


def summary_line(label: str, scanned: Scan) -> str:
    """One headline line for a folder."""
    result = scanned.result
    return (f"{label:8} purity {result.purity_score:>3}/100   flagged {result.flagged_file_count}/{len(result.findings)} files   "
            f"{scanned.tokens:,} tokens ({scanned.flagged_tokens:,} in flagged files)")


def cost_table(before: Scan, after: Scan | None, sessions: int) -> list[str]:
    """Rows of the per-model table, with the after columns when a refactored folder is given."""
    header = f"{'model':20}{'read before':>13}{'hotspot before':>16}"
    if after:
        header += f"{'read after':>13}{'hotspot after':>15}{'hotspot saved':>15}"
    rows = [header, "-" * len(header)]
    for model in load_prices():
        read_b, hot_b = costs(before, model, sessions)
        row = f"{model['name']:20}{'$' + format(read_b, '.2f'):>13}{'$' + format(hot_b, '.3f'):>16}"
        if after:
            read_a, hot_a = costs(after, model, sessions)
            saved = f"{(1 - hot_a / hot_b):.0%}" if hot_b else "-"
            row += f"{'$' + format(read_a, '.2f'):>13}{'$' + format(hot_a, '.3f'):>15}{saved:>15}"
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    """Print the before/after scores and the per-model cost table."""
    parser = argparse.ArgumentParser(description="Maintainability cost of a codebase for LLM agents, via Argon.")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path, nargs="?")
    parser.add_argument("--sessions", type=int, default=100, help="agent sessions that read the whole codebase (default 100)")
    args = parser.parse_args(argv)
    before = scan(args.before)
    after = scan(args.after) if args.after else None
    print(summary_line("before", before))
    if after:
        print(summary_line("after", after))
    print(f"\nUSD, {args.sessions} agent sessions reading everything; hotspot = rewrite each flagged file once\n")
    print("\n".join(cost_table(before, after, args.sessions)))
    print(f"\ntokens estimated at {CHARS_PER_TOKEN} chars each; prices from tools/model_prices.json (unverified list prices)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
