"""Benchmark token usage and cost of code BEFORE and AFTER an AST optimization, through OpenRouter.

    set OPENROUTER_API_KEY=sk-or-...          (PowerShell: $env:OPENROUTER_API_KEY="sk-or-...")
    python -m tools.openrouter_bench                              # built-in demo pair from tests/
    python -m tools.openrouter_bench before.py after.py           # two files, or two folders of .py files
    python -m tools.openrouter_bench before.py after.py --model openai/gpt-4o --html dashboard.html

Each text is sent once as a prompt ending in "Reply with the single word OK." (so the completion is
tiny and the numbers are about the code you send). The script prints the provider's own usage block:
prompt/completion/total tokens, cached tokens (OpenAI-style `cached_tokens` or Anthropic-style
`cache_read_input_tokens`), and the cost OpenRouter reports, then a before/after dashboard.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.request
from pathlib import Path

from openai import APIStatusError, OpenAI

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"  # free; swap for "openai/gpt-4o", "anthropic/claude-sonnet-5.5", ...
PRICE_MODEL = "openai/gpt-4o"  # list prices used for the "would cost" row, so free runs still show dollars
INSTRUCTION = "\n\nReply with the single word OK."
ROOT = Path(__file__).resolve().parent.parent
DEMO_BEFORE = ROOT / "tests" / "performance_fixtures" / "ai_generated_service_22k.py"
DEMO_AFTER = ROOT / "tests" / "refactor_examples" / "ai_generated_service_refactored.py"
BAR = 34


KEY_NAMES = ("OPENROUTER_API_KEY", "OPEN_ROUTE_API_KEY")  # the second spelling is accepted too


def _key_in(env_file: Path) -> str | None:
    """The key from one .env file (any KEY_NAMES variable), or None."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.is_file() else []
    pairs = (line.partition("=") for line in lines if not line.lstrip().startswith("#"))
    return next((value.strip().strip("\"'") for name, _, value in pairs if name.strip() in KEY_NAMES and value.strip()), None)


def make_client() -> OpenAI:
    """OpenRouter client; the key comes from a KEY_NAMES environment variable, else from a .env file (repo root or cwd)."""
    key = next(filter(None, map(os.environ.get, KEY_NAMES)), None) or next(filter(None, map(_key_in, (Path.cwd() / ".env", ROOT.parent / ".env"))), None)
    if not key:
        raise SystemExit("no OpenRouter key: save it in .env as OPENROUTER_API_KEY=... (or OPEN_ROUTE_API_KEY=...), or set that environment variable")
    return OpenAI(base_url=BASE_URL, api_key=key, max_retries=3)


def load(path: Path) -> str:
    """A file's text, or all .py files of a folder joined with a header line each."""
    if path.is_file():
        return path.read_text(encoding="utf-8")
    parts = [f"# === {p.relative_to(path).as_posix()} ===\n{p.read_text(encoding='utf-8')}" for p in sorted(path.rglob("*.py")) if "__pycache__" not in p.parts]
    return "\n\n".join(parts)


def list_prices(model: str) -> dict | None:
    """OpenRouter's USD-per-token prices for a model (public endpoint, no key needed), or None."""
    with urllib.request.urlopen(f"{BASE_URL}/models", timeout=30) as response:
        for entry in json.load(response)["data"]:
            if entry["id"] == model:
                return {key: float(value) for key, value in entry["pricing"].items() if value not in (None, "")}
    return None


def benchmark(text: str, model: str, client: OpenAI) -> dict:
    """Send `text` to `model` and return the usage numbers from the response payload."""
    reply = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": text + INSTRUCTION}],
        max_tokens=16,
        temperature=0,
        extra_body={"usage": {"include": True}},  # asks OpenRouter to report the cost too
    )
    usage = reply.usage.model_dump() if reply.usage else {}
    details = usage.get("prompt_tokens_details") or {}
    return {
        "prompt": usage.get("prompt_tokens", 0),
        "completion": usage.get("completion_tokens", 0),
        "total": usage.get("total_tokens", 0),
        "cached": details.get("cached_tokens") or usage.get("cache_read_input_tokens") or 0,  # OpenAI / Anthropic naming
        "cache_write": details.get("cache_write_tokens") or usage.get("cache_creation_input_tokens") or 0,
        "cost": usage.get("cost") or 0.0,
    }


def list_cost(metrics: dict, prices: dict | None) -> float:
    """What these tokens would cost at list price: uncached input, cached input, output."""
    if not prices:
        return 0.0
    rate = prices.get("prompt", 0.0)
    uncached = metrics["prompt"] - metrics["cached"]
    return uncached * rate + metrics["cached"] * prices.get("input_cache_read", rate) + metrics["completion"] * prices.get("completion", 0.0)


def print_usage(label: str, m: dict) -> None:
    """The raw token breakdown for one run."""
    print(f"[{label}] prompt_tokens={m['prompt']:,}  completion_tokens={m['completion']:,}  total_tokens={m['total']:,}  "
          f"cached_tokens={m['cached']:,}  cache_write_tokens={m['cache_write']:,}  reported_cost=${m['cost']:.6f}")


def rows(before: dict, after: dict, prices: dict | None) -> list[tuple[str, float, float, str]]:
    """Dashboard rows: (label, before, after, number format)."""
    return [
        ("Prompt tokens", before["prompt"], after["prompt"], ",.0f"),
        ("Total tokens", before["total"], after["total"], ",.0f"),
        ("Cached tokens", before["cached"], after["cached"], ",.0f"),
        ("Reported cost (USD)", before["cost"], after["cost"], ".6f"),
        (f"List-price cost @ {PRICE_MODEL}", list_cost(before, prices), list_cost(after, prices), ".4f"),
    ]


def terminal_dashboard(model: str, data: list[tuple[str, float, float, str]]) -> None:
    """Bars for before vs after, then the saving, one block per metric."""
    line = "=" * 70
    print(f"\n{line}\n  AST OPTIMIZATION - TOKEN BENCHMARK    model: {model}\n{line}")
    for label, old, new, fmt in data:
        top = max(old, new) or 1
        saved = old - new
        percent = f"{saved / old:.1%}" if old else "n/a"
        print(f"\n  {label}")
        print(f"    before  {'#' * round(BAR * old / top):<{BAR}}  {format(old, fmt)}")
        print(f"    after   {'#' * max(1 if new else 0, round(BAR * new / top)):<{BAR}}  {format(new, fmt)}")
        print(f"    saved   {format(saved, fmt)}  ({percent})")
    print(f"\n{line}")


def html_dashboard(path: Path, model: str, data: list[tuple[str, float, float, str]]) -> None:
    """The same numbers as one self-contained HTML page."""
    blocks = []
    for label, old, new, fmt in data:
        top = max(old, new) or 1
        change = (new - old) / old if old else 0.0
        blocks.append(
            f"<section><h2>{html.escape(label)} <b class={'g' if change <= 0 else 'r'}>{change:+.1%}</b></h2>"
            f"<div class=r><span>Before</span><i style='width:{100 * old / top:.1f}%' class=b></i><em>{format(old, fmt)}</em></div>"
            f"<div class=r><span>After</span><i style='width:{max(0.5 if new else 0, 100 * new / top):.1f}%' class=a></i><em>{format(new, fmt)}</em></div></section>")
    path.write_text(
        "<!doctype html><meta charset=utf-8><title>Token benchmark</title><style>"
        "body{font:16px system-ui;margin:40px auto;max-width:760px;padding:0 16px;background:#0f1115;color:#e8eaed}"
        "h1{font-size:22px}h2{font-size:15px;margin:26px 0 8px}h2 b{float:right}.g{color:#4ade80}.r{color:#f87171}.r{display:flex;align-items:center;gap:10px;margin:4px 0}"
        ".r span{width:56px;color:#9aa0a6}.r i{height:18px;border-radius:3px}.b{background:#f87171}.a{background:#4ade80}.r em{font-style:normal;font-variant-numeric:tabular-nums}"
        f"</style><h1>AST optimization - token benchmark</h1><p>model: {html.escape(model)}</p>{''.join(blocks)}", encoding="utf-8")


def main(before: str, after: str, args: argparse.Namespace) -> int:
    """Run both texts through the model and show the comparison."""
    prices = list_prices(PRICE_MODEL)
    own = list_prices(args.model)
    guess = (len(before) + len(after)) / 4 * ((own or {}).get("prompt", 0.0))
    if guess > args.max_usd and not args.yes:
        print(f"{args.model} would cost about ${guess:.2f} for these two prompts (cap ${args.max_usd:.2f}); re-run with --yes to proceed", file=sys.stderr)
        return 2
    client = make_client()
    try:
        results = []
        for label, text in (("BEFORE", before), ("AFTER", after)):
            for _ in range(args.repeat):  # a repeat shows prompt caching: the 2nd identical call can report cached tokens
                metrics = benchmark(text, args.model, client)
            print_usage(label, metrics)
            results.append(metrics)
    except APIStatusError as error:
        print(f"OpenRouter error {error.status_code}: {error.message}", file=sys.stderr)
        return 1
    data = rows(results[0], results[1], prices)
    terminal_dashboard(args.model, data)
    if args.html:
        html_dashboard(args.html, args.model, data)
        print(f"  HTML dashboard written to {args.html}")
    return 0


if __name__ == "__main__":
    # --- Paste your code here, or pass two file/folder paths on the command line, or leave both empty for the demo pair. ---
    BEFORE = ""  # bloated AI-generated code
    AFTER = ""  # your optimized code
    parser = argparse.ArgumentParser(description="OpenRouter token benchmark: before vs after.")
    parser.add_argument("before", nargs="?", type=Path)
    parser.add_argument("after", nargs="?", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--repeat", type=int, default=1, help="calls per text; 2 lets a caching provider report cached tokens")
    parser.add_argument("--html", type=Path, help="also write a self-contained HTML dashboard")
    parser.add_argument("--max-usd", type=float, default=1.0, help="refuse to run a paid model above this estimated cost")
    parser.add_argument("--yes", action="store_true", help="override --max-usd")
    options = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if options.before and options.after:
        BEFORE, AFTER = load(options.before), load(options.after)
    elif not (BEFORE and AFTER):
        BEFORE, AFTER = load(DEMO_BEFORE), load(DEMO_AFTER)
    raise SystemExit(main(BEFORE, AFTER, options))
