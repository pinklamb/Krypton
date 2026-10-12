"""Dataset benchmark: AI-written Python, refactored by an LLM with Argon in the loop, tokens before vs after.

    python -m tools.dataset_bench                      # HumanVSAI_CodeDataset.zip in the repo root, free model
    python -m tools.dataset_bench --limit 10 --rounds 2 --html dashboard.html
    python -m tools.dataset_bench --model openai/gpt-4o --yes
    python -m tools.dataset_bench --mock               # offline dry run of the whole pipeline; numbers are NOT measurements

Steps: (1) read the ChatGPT-written Python samples, (2) keep the ones Argon has refactoring hints for,
(3) for each, show the model Argon's hints and ask it to refactor, re-run Argon, repeat up to --rounds
(a version is kept only if it parses and Argon reports no more hints than before), (4) send the joined
"before" and joined "after" code to the model once each and print the provider's token usage.
The dataset's code is never executed, only parsed, so behavior is NOT verified; read the diffs before trusting them.
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import random
import re
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

from openai import APIStatusError

from argon.engine.config import EngineConfig, apply_level
from argon.engine.run import run_local
from tools.openrouter_bench import DEFAULT_MODEL, ROOT, benchmark, html_dashboard, list_prices, make_client, print_usage, rows, terminal_dashboard

DEFAULT_DATA = ROOT.parent / "HumanVSAI_CodeDataset.zip"
REFACTOR_PROMPT = """Refactor this Python program so it is shorter and structurally simpler without changing what it reads or prints.
Argon, a structural-weight checker, reports these problems:
{hints}

Rules: keep behavior identical; no comments or docstrings; flatten nesting with early returns or helpers; keep list comprehensions.
Reply with ONLY the complete program in one ```python block.

```python
{code}
```"""


def read_dataset(path: Path) -> list[dict]:
    """Rows of HumanVsAI_CodeDataset.csv, found inside (nested) zip files or read directly."""
    data = path.read_bytes()
    while data[:2] == b"PK":
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in archive.namelist() if "__macosx" not in n.lower() and n.lower().endswith(("humanvsai_codedataset.csv", ".zip"))]
        data = archive.read(names[0])
    csv.field_size_limit(10**9)
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))


def review(code: str, cfg: EngineConfig) -> tuple[int, dict]:
    """(flagged file count, refactoring hints) Argon reports for one snippet."""
    result = run_local({"sample.py": code.encode("utf-8")}, None, cfg)
    return result.flagged_file_count, result.hints


def pick(data: list[dict], limit: int, seed: int, cfg: EngineConfig) -> list[str]:
    """A seeded sample of ChatGPT-written Python snippets that Argon has hints for."""
    pool = [row["Sample_Code"] for row in data if row["Language"] == "Python" and row["Generated"] == "AI"]
    heavy = [code for code in pool if review(code, cfg)[1]]
    print(f"{len(pool)} AI-written Python samples; Argon has hints for {len(heavy)}; using {min(limit, len(heavy))}")
    random.Random(seed).shuffle(heavy)
    return heavy[:limit]


def extract_code(reply: str) -> str | None:
    """The first ```python block of a reply, or the whole reply if it has no fence; None if it is not valid Python."""
    match = re.search(r"```(?:python)?\n(.*?)```", reply, re.S)
    code = (match.group(1) if match else reply).strip("\n") + "\n"
    try:
        ast.parse(code)
    except SyntaxError:
        return None
    return code


def calls(code: str) -> set[str]:
    """Names of the plain functions a program calls (print, input, ...)."""
    return {node.func.id for node in ast.walk(ast.parse(code)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def usable(original: str, candidate: str | None) -> bool:
    """A refactor is plausible only if it parses, is not trivially short, and still makes the original's print/input calls."""
    if not candidate or len(candidate) < len(original) // 4:
        return False
    return {"print", "input"} & calls(original) <= calls(candidate)


def ask(client, model: str, prompt: str, spend: dict) -> str:
    """One refactoring call; adds its tokens and cost to `spend` and returns the reply text ('' if the model never answered)."""
    reply = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=8192,
                                           extra_body={"usage": {"include": True}, "reasoning": {"effort": "low"}})  # reasoning models can burn the whole budget thinking
    usage = reply.usage.model_dump() if reply.usage else {}
    spend["calls"] += 1
    spend["tokens"] += usage.get("total_tokens", 0)
    spend["cost"] += usage.get("cost") or 0.0
    return reply.choices[0].message.content or ""


def refactor(code: str, client, model: str, cfg: EngineConfig, rounds: int, spend: dict) -> str:
    """Show the model Argon's hints and ask for a refactor, up to `rounds` times; keep only versions that parse and do not add hints."""
    current = code
    for _ in range(rounds):
        _, hints = review(current, cfg)
        if not hints:
            break
        prompt = REFACTOR_PROMPT.format(hints="\n".join(f"- {where}: {text}" for where, text in hints.items()), code=current)
        candidate = extract_code(ask(client, model, prompt, spend))
        good = usable(code, candidate) and len(review(candidate, cfg)[1]) <= len(hints)
        spend["rejected"] += not good
        current = candidate if good else current
    return current


class MockClient:
    """Offline stand-in for the API: 'refactors' by dropping comments and blank lines, and invents usage from text length."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request) -> SimpleNamespace:
        """A response shaped like the OpenAI SDK's."""
        text = request["messages"][0]["content"]
        body = ""
        if "```python" in text:
            program = text.split("```python\n")[-1].rsplit("```", 1)[0]
            body = "```python\n" + "\n".join(line for line in program.splitlines() if line.strip() and not line.strip().startswith("#")) + "\n```"
        tokens = len(text) // 4
        usage = {"prompt_tokens": tokens, "completion_tokens": len(body) // 4, "total_tokens": tokens + len(body) // 4, "cost": 0.0}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=body))], usage=SimpleNamespace(model_dump=lambda: usage))


def join(codes: list[str]) -> str:
    """All snippets as one prompt, each under a header line."""
    return "\n\n".join(f"# === sample {i} ===\n{code}" for i, code in enumerate(codes, start=1))


def main(args: argparse.Namespace) -> int:
    """Pick samples, refactor them with Argon in the loop, benchmark before and after, show the dashboard."""
    cfg = apply_level(EngineConfig(), args.level)
    if not (args.mock or args.model.endswith(":free") or args.yes):
        print(f"{args.model} is a paid model: about {args.limit * args.rounds + 2} requests will be billed; re-run with --yes", file=sys.stderr)
        return 2
    before = pick(read_dataset(args.data), args.limit, args.seed, cfg)
    client = MockClient() if args.mock else make_client()
    spend = {"calls": 0, "tokens": 0, "cost": 0.0, "rejected": 0}
    try:
        after = []
        for i, code in enumerate(before, start=1):
            after.append(refactor(code, client, args.model, cfg, args.rounds, spend))
            print(f"  refactored {i}/{len(before)}  ({spend['calls']} calls, {spend['rejected']} rejected so far)", flush=True)
        results = [benchmark(join(codes), args.model, client) for codes in (before, after)]
    except APIStatusError as error:
        print(f"OpenRouter error {error.status_code}: {error.message}", file=sys.stderr)
        return 1
    tag = " (MOCK - not measurements)" if args.mock else ""
    for i, (old, new) in enumerate(zip(before, after), start=1):
        print(f"  sample {i:>2}: {len(old):>5} -> {len(new):>5} chars   hints {len(review(old, cfg)[1])} -> {len(review(new, cfg)[1])}{'' if old != new else '   (unchanged)'}")
    print_usage("BEFORE", results[0])
    print_usage("AFTER", results[1])
    hints = [sum(len(review(code, cfg)[1]) for code in codes) for codes in (before, after)]
    extra = [("Argon hints", hints[0], hints[1], ",.0f"), ("Characters", len("".join(before)), len("".join(after)), ",.0f")]
    data = rows(results[0], results[1], list_prices("openai/gpt-4o")) + extra
    terminal_dashboard(args.model + tag, data)
    saved = results[0]["prompt"] - results[1]["prompt"]
    print(f"  Cost of producing 'after': {spend['tokens']:,} tokens, ${spend['cost']:.4f}, {spend['calls']} model calls, {spend['rejected']} replies rejected (empty, unparsable, trivially short, or dropped print/input)")
    print("  " + (f"Pays for itself after the code is sent as a prompt ~{spend['tokens'] / saved:.1f} times" if saved > 0 else "No prompt-token saving on this sample"))
    if args.html:
        html_dashboard(args.html, args.model + tag, data)
        print(f"  HTML dashboard written to {args.html}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refactor AI-written code with Argon in the loop and benchmark tokens.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="HumanVSAI_CodeDataset.zip (nested zips are fine) or the CSV")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=8, help="samples to refactor (free models allow few requests per day)")
    parser.add_argument("--rounds", type=int, default=2, help="refactor attempts per sample")
    parser.add_argument("--level", choices=["relaxed", "standard", "strict"], default="standard")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--mock", action="store_true", help="no API calls at all")
    parser.add_argument("--yes", action="store_true", help="confirm spending on a paid model")
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(parser.parse_args()))
