"""Files too big to analyze safely: reported as not checked, with the setting that would check them.

These files are left out of the findings and the purity score instead of being scored as
failures. The result carries one entry per error code, `{code: explanation}`, so nothing is
silently passed and the report stays small however many files are affected.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from argon.engine.config import EngineConfig

# Measured on a Python file built from the 22k-line fixture (Windows, ast-grep-py 0.45).
SECONDS_PER_MB = 2
MEGABYTES_PER_MB = 55
LISTED_FILES = 5  # names shown per code; the rest are counted

FILE_TOO_LARGE = "E_FILE_TOO_LARGE"
LINE_TOO_LONG = "E_LINE_TOO_LONG"
NODE_TOO_WIDE = "E_NODE_TOO_WIDE"
NOTHING_CHECKED = "E_NOTHING_CHECKED"


@dataclass(frozen=True)
class Unchecked:
    """One file the engine refused to analyze: its error code and the limits it passed."""

    filename: str
    code: str
    size: int  # bytes
    longest_line: int  # characters


def build_errors(items: list[Unchecked], checked: int, cfg: EngineConfig) -> dict[str, str]:
    """{error code: explanation} for every kind of refusal, plus one if nothing was checked."""
    errors = {code: _explain(code, [i for i in items if i.code == code], cfg) for code in dict.fromkeys(i.code for i in items)}
    if items and not checked:
        errors[NOTHING_CHECKED] = "No file was checked, so a purity score of 100 means nothing."
    return errors


def _explain(code: str, items: list[Unchecked], cfg: EngineConfig) -> str:
    """One sentence per code: the files, the limit they passed, and the setting that fixes it."""
    limit, fix = _limit_and_fix(code, items, cfg)
    return (
        f"{_names(items)} not checked and left out of the score: {limit}. "
        f"Fix: set {fix} in a --config file ({_cost(items)})."
    )


def _names(items: list[Unchecked]) -> str:
    """Up to LISTED_FILES filenames, then a count of the rest."""
    shown = ", ".join(item.filename for item in items[:LISTED_FILES])
    more = len(items) - LISTED_FILES
    return f"{shown} and {more} more" if more > 0 else shown


def _limit_and_fix(code: str, items: list[Unchecked], cfg: EngineConfig) -> tuple[str, str]:
    """The limit that was passed, and the config line that raises it enough for all these files."""
    if code == FILE_TOO_LARGE:
        biggest = max(item.size for item in items)
        return f"over max_file_bytes ({cfg.max_file_bytes:,}); the largest is {biggest:,} bytes", f"max_file_bytes = {ceil(biggest / 1_000_000) * 1_000_000}"
    if code == LINE_TOO_LONG:
        longest = max(item.longest_line for item in items)
        return (
            f"a line over max_line_chars ({cfg.max_line_chars:,}); the longest is {longest:,} characters (minified code belongs in generated_path_globs)",
            f"max_line_chars = {ceil(longest / 1000) * 1000}",
        )
    return (
        f"a node with over max_children ({cfg.max_children:,}) children whose depth rules need them read together (such as one case body or branch with that many statements)",
        f"max_children = {cfg.max_children * 2}",
    )


def _cost(items: list[Unchecked]) -> str:
    """Rough time and memory to analyze the largest file once its limit is raised."""
    largest = max(item.size for item in items) / 1e6
    return f"about {ceil(largest * SECONDS_PER_MB)} s and {ceil(30 + largest * MEGABYTES_PER_MB)} MB for the largest, measured on Python"
