"""How different is the structure of AI-assisted code from the rest of the same repository?

    python -m tools.ai_human_gap REPO --subdir src/filesystem

Every function in the current code is scored by Krypton and attributed by `git blame`: the share of its lines
whose last change was a commit that credits an AI assistant (a Co-Authored-By or "Generated with" trailer naming it).
Functions at least half AI are compared with the rest, in size bands, so a difference in how big the functions are
is not mistaken for a difference in how tangled they are. It measures a difference in structure; it says nothing
about which is better, and the trailers show involvement, not authorship.
"""

from __future__ import annotations

import argparse
import random
import re
import statistics
import subprocess
from pathlib import Path

from krypton.engine.run import run_local

AI_TRAILER = re.compile(r"co-authored-by:.*claude|generated with.*claude", re.IGNORECASE)
BANDS = ((5, 15), (15, 30), (30, 60), (60, 10**6))
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return done.stdout


def ai_commits(repo: Path) -> set[str]:
    """Hashes of non-merge commits whose message credits an AI assistant."""
    chunks = git(repo, "log", "--no-merges", "--format=@@%H%x01%B").split("@@")[1:]
    return {chunk.partition("\x01")[0].strip() for chunk in chunks if AI_TRAILER.search(chunk.partition("\x01")[2])}


def blame_flags(repo: Path, path: str, ai: set[str]) -> list[bool]:
    """One bool per line of `path` at HEAD: was its last change an AI-credited commit (whitespace and moved lines followed)."""
    flags, sha = [], ""
    for line in git(repo, "blame", "-w", "-M", "--line-porcelain", "HEAD", "--", path).splitlines():
        if re.match(r"^[0-9a-f]{40} \d+ \d+", line):
            sha = line.split()[0]
        elif line.startswith("\t"):
            flags.append(sha in ai)
    return flags


def is_test(name: str) -> bool:
    low = name.lower()
    return "test" in low or "__tests__" in low or "/spec" in low


def collect(repo: Path, subdir: str, min_lines: int = 5) -> list[dict]:
    """One row per function of the current code (tests excluded): its size, structure, and AI share of lines."""
    repo = repo.resolve()
    names = [n for n in git(repo, "ls-files", "--", subdir).splitlines() if n.endswith(SOURCE_SUFFIXES) and not is_test(n)]
    result = run_local({n: (repo / n).read_bytes() for n in names})
    ai, rows = ai_commits(repo), []
    for finding in result.findings:
        if not finding.functions:
            continue
        flags = blame_flags(repo, finding.filename, ai)
        for fn in finding.functions:
            span = flags[fn.start_line - 1:fn.end_line]
            if len(span) >= min_lines:
                rows.append({"file": finding.filename, "fn": fn.name, "ai": sum(span) / len(span), "lines": len(span), "branches": fn.branches,
                             "depth": fn.max_depth, "over": fn.rating_master > result.threshold, "nested": bool(fn.nested)})
    return rows


def split(rows: list[dict], cut: float = 0.5) -> tuple[list[dict], list[dict]]:
    return [r for r in rows if r["ai"] >= cut], [r for r in rows if r["ai"] < cut]


def rate(rows: list[dict], key: str = "over") -> float | None:
    return 100 * sum(r[key] for r in rows) / len(rows) if rows else None


def density(rows: list[dict]) -> float | None:
    """Decision points per line of code."""
    lines = sum(r["lines"] for r in rows)
    return sum(r["branches"] for r in rows) / lines if lines else None


def bootstrap_gap(ai: list[dict], rest: list[dict], draws: int = 4000, seed: int = 1) -> tuple[float, float] | None:
    """95% interval of (AI flag rate - rest flag rate) in points, resampling functions."""
    if not ai or not rest:
        return None
    rng = random.Random(seed)
    gaps = sorted(rate(rng.choices(ai, k=len(ai))) - rate(rng.choices(rest, k=len(rest))) for _ in range(draws))
    return gaps[int(0.025 * draws)], gaps[int(0.975 * draws) - 1]


def report(rows: list[dict]) -> dict:
    """The comparison: overall, by size band, and per decision-point density."""
    ai, rest = split(rows)
    bands = []
    for lo, hi in BANDS:
        a, r = ([x for x in group if lo <= x["lines"] < hi] for group in (ai, rest))
        bands.append({"lines": f"{lo}-{hi}" if hi < 10**6 else f"{lo}+", "ai_n": len(a), "ai_over": rate(a), "rest_n": len(r), "rest_over": rate(r)})
    return {"functions": len(rows), "ai_n": len(ai), "rest_n": len(rest), "ai_over": rate(ai), "rest_over": rate(rest),
            "ai_median_lines": statistics.median(r["lines"] for r in ai) if ai else None, "rest_median_lines": statistics.median(r["lines"] for r in rest) if rest else None,
            "ai_density": density(ai), "rest_density": density(rest), "gap_ci": bootstrap_gap(ai, rest), "bands": bands}


def fmt(value, spec: str = ".0f", unit: str = "") -> str:
    return "-" if value is None else f"{value:{spec}}{unit}"


def render(rep: dict) -> str:
    out = [f"{rep['functions']} functions; {rep['ai_n']} are at least half AI-credited lines, {rep['rest_n']} are not",
           f"                     functions  median lines  over the limit  decision points per line",
           f"  mostly AI          {rep['ai_n']:>9}  {fmt(rep['ai_median_lines']):>12}  {fmt(rep['ai_over'], '.0f', '%'):>14}  {fmt(rep['ai_density'], '.3f'):>23}",
           f"  the rest           {rep['rest_n']:>9}  {fmt(rep['rest_median_lines']):>12}  {fmt(rep['rest_over'], '.0f', '%'):>14}  {fmt(rep['rest_density'], '.3f'):>23}"]
    if rep["gap_ci"]:
        out.append(f"  flag-rate gap (AI minus rest): 95% interval {rep['gap_ci'][0]:.0f} to {rep['gap_ci'][1]:.0f} points")
    out.append("\nSame comparison inside size bands (lines per function), over the limit:")
    out += [f"  {b['lines']:>6} lines   AI {b['ai_n']:>3} functions {fmt(b['ai_over'], '.0f', '%'):>5}   rest {b['rest_n']:>3} functions {fmt(b['rest_over'], '.0f', '%'):>5}" for b in rep["bands"]]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", type=Path)
    parser.add_argument("--subdir", default=".")
    args = parser.parse_args()
    print(render(report(collect(args.repo, args.subdir))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
