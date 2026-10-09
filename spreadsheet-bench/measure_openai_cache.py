"""Compare real OpenAI prompt-cache usage for alphabetical and Argon file order.

Run from any directory with OPENAI_API_KEY configured:
    python spreadsheet-bench/measure_openai_cache.py

This sends the Python sources in ../argon/argon to the Responses API. The report
contains usage and prices, never source text or the API key.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
ARGON_ROOT = WORKSPACE / "argon"
DEFAULT_SOURCE = ARGON_ROOT / "argon"
MODEL = "gpt-6-luna"
REASONING_EFFORT = "medium"
PRICE_PER_MILLION = {"input": 0.10, "cached": 0.01, "write": 0.125, "output": 0.50}
QUESTION = "Review this Python codebase for this benchmark. Reply with only OK."
SCHEDULE = [
    ("cold_start", None),
    ("no_change", None),
    ("edit_volatile", "volatile"),
    ("edit_middle", "middle"),
    ("edit_stable", "stable"),
    ("no_change_again", None),
]


def read_corpus(folder: Path) -> dict[str, str]:
    ignored = {"__pycache__", "tests", "fixtures", "performance_fixtures", "refactor_examples"}
    files = {}
    for path in sorted(folder.rglob("*.py")):
        if ignored.intersection(path.parts):
            continue
        files[path.relative_to(folder).as_posix()] = path.read_text(encoding="utf-8")
    if not files:
        raise ValueError(f"No Python source files found under {folder}")
    return files


def argon_order(folder: Path) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-m", "argon.cli", str(folder), "--order-by-volatility"],
        cwd=ARGON_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return [item["filename"] for item in json.loads(done.stdout)["files"]]
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError(f"Argon could not provide an ordering: {done.stderr.strip()}") from error


def cache_breaks(order: list[str], corpus: dict[str, str]) -> set[int]:
    total = sum(len(corpus[name]) for name in order)
    targets, breaks, seen = (0.25, 0.50, 0.75), set(), 0
    next_target = 0
    for index, name in enumerate(order[:-1]):
        seen += len(corpus[name])
        while next_target < len(targets) and seen >= total * targets[next_target]:
            breaks.add(index)
            next_target += 1
    return breaks


def request_input(
    order: list[str], corpus: dict[str, str], edits: dict[str, int], breaks: set[int]
) -> list[dict]:
    content = []
    for index, name in enumerate(order):
        source = corpus[name] + "\n" + "# benchmark edit\n" * edits.get(name, 0)
        block = {"type": "input_text", "text": f"### {name}\n{source}"}
        if index in breaks:
            block["prompt_cache_breakpoint"] = {"mode": "explicit"}
        content.append(block)
    return [
        {"role": "developer", "content": content},
        {"role": "user", "content": QUESTION},
    ]


def api_request(payload: dict, api_key: str) -> dict:
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach the OpenAI API: {error.reason}") from error


def turn_cost(usage: dict) -> tuple[float, float, int, int]:
    details = usage.get("input_tokens_details") or {}
    input_tokens = int(usage.get("input_tokens", 0))
    cached = int(details.get("cached_tokens", 0))
    written = int(details.get("cache_write_tokens", 0))
    output = int(usage.get("output_tokens", 0))
    ordinary = max(0, input_tokens - cached - written)
    paid = (
        ordinary * PRICE_PER_MILLION["input"]
        + cached * PRICE_PER_MILLION["cached"]
        + written * PRICE_PER_MILLION["write"]
        + output * PRICE_PER_MILLION["output"]
    ) / 1_000_000
    uncached = (input_tokens * PRICE_PER_MILLION["input"] + output * PRICE_PER_MILLION["output"]) / 1_000_000
    return paid, uncached, cached, written


def run_order(
    label: str,
    order: list[str],
    corpus: dict[str, str],
    budget: float,
    spend: float,
    key: str,
) -> tuple[list[dict], float]:
    edits = {"stable": order[0], "middle": order[len(order) // 2], "volatile": order[-1]}
    breakpoints = cache_breaks(order, corpus)
    turns, previous_response = [], None
    for index, (step, target) in enumerate(SCHEDULE):
        if target:
            edits[edits[target]] += 1
        payload = {
            "model": MODEL,
            "reasoning": {"effort": REASONING_EFFORT},
            "max_output_tokens": 256,
            "store": False,
            "prompt_cache_key": key,
            "prompt_cache_options": {
                "mode": "implicit",
                **({"comparison_response_id": previous_response} if previous_response else {}),
            },
            "input": request_input(order, corpus, edits, breakpoints),
        }
        response = api_request(payload, os.environ["OPENAI_API_KEY"])
        usage = response.get("usage") or {}
        paid, uncached, cached, written = turn_cost(usage)
        spend += paid
        if spend > budget:
            raise RuntimeError(f"Measured spend ${spend:.6f} exceeded the ${budget:.2f} safety cap")
        turns.append(
            {
                "order": label,
                "turn": index + 1,
                "step": step,
                "response_id": response.get("id"),
                "input_tokens": usage.get("input_tokens", 0),
                "cached_tokens": cached,
                "cache_write_tokens": written,
                "output_tokens": usage.get("output_tokens", 0),
                "cost_usd": round(paid, 8),
                "uncached_cost_usd": round(uncached, 8),
                "prompt_cache_diagnostics": response.get("prompt_cache_diagnostics"),
            }
        )
        previous_response = response.get("id")
        time.sleep(0.25)
    return turns, spend


def summarize(turns: list[dict]) -> dict:
    input_tokens = sum(row["input_tokens"] for row in turns)
    cached = sum(row["cached_tokens"] for row in turns)
    return {
        "requests": len(turns),
        "input_tokens": input_tokens,
        "cached_tokens": cached,
        "cache_write_tokens": sum(row["cache_write_tokens"] for row in turns),
        "output_tokens": sum(row["output_tokens"] for row in turns),
        "cache_hit_rate": round(cached / input_tokens, 4) if input_tokens else 0,
        "cost_usd": round(sum(row["cost_usd"] for row in turns), 8),
        "uncached_cost_usd": round(sum(row["uncached_cost_usd"] for row in turns), 8),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure real GPT-6 Luna prompt-cache usage for Argon versus alphabetical order.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="multi-file Python source folder (default: argon/argon)")
    parser.add_argument("--max-usd", type=float, default=0.50, help="stop after measured spend exceeds this amount")
    parser.add_argument("--out", type=Path, default=HERE / "openai-cache-measurement.json")
    args = parser.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is not configured; set it in your shell (do not paste it into chat)")

    corpus = read_corpus(args.source.resolve())
    order = argon_order(args.source.resolve())
    if set(order) != set(corpus):
        raise RuntimeError("Argon ordering did not match the source corpus")
    plans = [("argon_stable_first", order), ("alphabetical", sorted(corpus))]
    rough_tokens = sum(len(text) + len(name) + 16 for name, text in corpus.items()) / 2
    worst_estimate = (
        rough_tokens * len(SCHEDULE) * len(plans) * PRICE_PER_MILLION["write"]
        + 256 * len(SCHEDULE) * len(plans) * PRICE_PER_MILLION["output"]
    ) / 1_000_000
    print(f"Model: {MODEL}, reasoning: {REASONING_EFFORT}; {len(corpus)} files")
    print(f"Conservative preflight estimate: about ${worst_estimate:.4f}; spend cap: ${args.max_usd:.2f}")
    if worst_estimate > args.max_usd:
        raise RuntimeError("Preflight estimate exceeds --max-usd; use a smaller source folder or raise the cap")

    run_id = uuid.uuid4().hex[:10]
    all_turns, spend = [], 0.0
    for label, file_order in plans:
        turns, spend = run_order(label, file_order, corpus, args.max_usd, spend, f"argon-cache-{run_id}-{label}")
        all_turns.extend(turns)
    grouped = {label: summarize([row for row in all_turns if row["order"] == label]) for label, _ in plans}
    report = {
        "measurement": "OpenAI Responses API telemetry",
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "source_folder": str(args.source.resolve()),
        "source_files": len(corpus),
        "source_chars": sum(map(len, corpus.values())),
        "prices_usd_per_million_tokens": PRICE_PER_MILLION,
        "prompt_cache_mode": "implicit with explicit file-group breakpoints",
        "note": "Only usage metadata is saved; prompts and source text are not written to the report.",
        "summary": grouped,
        "turns": all_turns,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(grouped, indent=2))
    print(f"Saved usage report to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
