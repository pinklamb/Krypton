"""Score source text with the Krypton engine and print a small JSON summary.

Reads {path: text} as JSON on stdin. The engine only parses: nothing from the repository is imported,
compiled or run, and no repository file exists on disk. Runs as its own process so a hard repository
can be stopped by a timeout and a memory limit without touching the web server.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

CHARS_PER_TOKEN = 3.5  # the same rough estimate the engine uses for its file manifest
WORST = 8
EXAMPLES = 6
EXCLUDED_REASONS = ("vendored_path", "vendored_name", "vendored_content", "generated_path")   # files the engine left out as libraries or generated code


def limit_resources() -> None:
    """Cap memory and CPU time where the platform allows it."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    except (ImportError, ValueError, OSError):
        pass


def rating_of(function, mode: str) -> float:
    """The rating the flag threshold applies to."""
    return function.rating_master if mode == "master" else function.rating_nesting


def tangled_functions(result) -> list[tuple[float, str, str, int]]:
    """(times over the threshold, file, function, first line) for every function at or over it, worst first."""
    limit = result.threshold or 1.0
    over = [(rating_of(fn, result.rating_mode) / limit, file.filename, fn.name, fn.start_line) for file in result.findings for fn in file.functions if fn.exempt_reason is None]
    return sorted((row for row in over if row[0] >= 1.0), reverse=True)


def worst_rows(tangled: list[tuple[float, str, str, int]]) -> list[dict]:
    """The few worst functions, as the page shows them."""
    return [{"times": round(times, 1), "file": name, "function": function, "line": line} for times, name, function, line in tangled[:WORST]]


def totals(result) -> dict:
    """Counts straight from the engine's result."""
    return {
        "files": len(result.findings),
        "skipped": result.skipped_file_count,
        "lines": sum(file.line_count for file in result.findings),
        "functions": sum(len(file.functions) for file in result.findings),
        "purity": result.purity_score,
        "flagged_files": result.flagged_file_count,
        "unparseable": sum(1 for file in result.findings if file.parse_error),
        "languages": dict(Counter(file.language for file in result.findings)),
    }


def excluded(result) -> dict:
    """How many files were left out as bundled libraries or generated code, with a few example paths."""
    names = sorted(skip.filename for skip in result.skipped_files if skip.reason in EXCLUDED_REASONS)
    return {"count": len(names), "examples": names[:EXAMPLES]}


def summarize(result, sources: dict[str, str]) -> dict:
    """The numbers the page shows; no source text."""
    tangled = tangled_functions(result)
    characters = sum(len(sources[file.filename]) for file in result.findings)
    return {**totals(result), "tokens": round(characters / CHARS_PER_TOKEN), "tangled": len(tangled), "worst": worst_rows(tangled), "excluded": excluded(result)}


def main() -> int:
    """stdin JSON in, summary JSON out."""
    limit_resources()
    sources = json.load(sys.stdin)
    attributes = sources.pop(".gitattributes", "")
    from krypton.engine.config import DEFAULT_CONFIG
    from krypton.engine.run import run_local
    from krypton.engine.vendored import linguist_globs

    vendored, generated = linguist_globs(attributes)
    cfg = DEFAULT_CONFIG.model_copy(update={"vendored_path_globs": DEFAULT_CONFIG.vendored_path_globs + vendored, "generated_path_globs": DEFAULT_CONFIG.generated_path_globs + generated})
    result = run_local({name: text.encode("utf-8") for name, text in sources.items()}, cfg=cfg)
    json.dump(summarize(result, sources), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
