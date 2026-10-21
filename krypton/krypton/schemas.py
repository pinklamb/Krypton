"""Data models shared by the engine, the CLI and (later) Phase 1 (spec §4).

All of master §3.2's FileFinding fields keep their names; new fields are only added.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


FlagReason = Literal[
    "concentration_rule",  # master §4.1
    "semantic_path_logic",  # master §4.2
    "unparseable",  # §5.2, fail closed (C2)
    "function_overload",  # a function has too many decisions or too heavy a nesting score
    "file_overload",  # the file's total nesting score is over budget
    "coupling_hotspot",  # file wired to many others (imports or shared globals)
]
# When several reasons apply, flag_reason takes the first in this order.
FLAG_PRIORITY: tuple[FlagReason, ...] = ("unparseable", "concentration_rule", "semantic_path_logic", "function_overload", "file_overload", "coupling_hotspot")


class FunctionFinding(BaseModel):
    """One function's measurements and both ratings (spec §4)."""

    name: str  # qualified: "Cart.total", "handler" (§8.1)
    start_line: int  # 1-based
    end_line: int
    score: int  # own weighted decisions + taxed inner scopes (§8)
    branches: int  # decisions written directly in this function, no base
    max_depth: int  # reported, 1 = flat; effective depth (§8.2, §8.4)
    effective_lines: int  # §9.4
    effective_lines_altered: int
    determinate_outputs: int  # raw distinct outputs (§9.1)
    outputs_effective: int  # min(raw, branches + 1), at least 1
    rating_master: float  # §10.4
    rating_nesting: float  # §10.4
    nested: bool  # max_depth >= 3
    saturated: bool = False  # score hit SCORE_CAP (B3)


class FileFinding(BaseModel):
    """One file's measurements, flag and functions; master §3.2 fields plus additions (spec §4)."""

    # master §3.2 fields
    filename: str  # POSIX, relative to scan root
    line_count: int  # physical lines, display only (rules use effective_lines)
    async_boundary_count: int
    cyclomatic_branches: int  # flat count, whole file, no base (B5)
    lines_altered: int  # physical, display only
    determinate_outputs: int  # sum of functions' outputs_effective
    relative_import_count: int  # now ALL imports resolving to a scanned file (A8)
    logic_complexity_rating: float  # the rating used for gating (§10.4)
    flagged: bool
    flag_reason: Optional[FlagReason] = None
    # added
    language: Literal["javascript", "typescript", "tsx", "python"]
    effective_lines: int
    effective_lines_altered: int
    nesting_score: int  # weighted, whole file incl. module level
    max_depth: int  # deepest effective decision in the file
    parse_error: bool
    error_coverage: float  # 0.0-1.0 share of characters inside ERROR / MISSING
    functions: list[FunctionFinding]
    trivial_wrapper_count: int = 0  # named functions with <= 1 branch and <= 4 effective lines
    overhead_tax: float = 0.0  # purity points this file's wrapper density costs (0 below the free share)
    coupling_weight: int = 0  # files this one depends on + files depending on it
    change_frequency: Optional[float] = None  # 0.0 stable .. 1.0 volatile; None = not measured


class SkippedFile(BaseModel):
    """A file that was not analyzed, and why."""

    filename: str
    reason: Literal["generated_path", "vendored_path", "vendored_name", "vendored_content", "unsupported_extension"]


class EngineResult(BaseModel):
    """Everything one run produces: score, conclusion, findings, skips and the summary markdown."""

    purity_score: int  # 0-100
    threshold: float = 0.0  # the flag threshold in force (after --level), for the active rating mode
    check_conclusion: Literal["success", "failure"]
    rating_mode: Literal["master", "nesting"]
    findings: list[FileFinding]
    flagged_file_count: int
    skipped_file_count: int
    skipped_files: list[SkippedFile]
    hints: dict[str, str] = {}  # "file:line function" (or a file name) -> where it is heavy and what to try
    hint_regions: dict[str, list[int]] = {}  # hint key -> [first line, last line] of the block it names
    progress: dict[str, str] = {}  # filled by --history: purity trend and per-function movement
    errors: dict[str, str] = {}  # error code -> explanation; files too large to check are left out of the score
    ignored_generated_markers: list[str]  # files whose marker was not honored (A3)
    fragmentation_flagged: bool
    fragmentation_cluster: list[str]  # files in the triggering group (§10.2)
    summary_markdown: str
