"""Every tunable constant (spec §10.1), plus the path-glob matcher the rules share."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict

from argon.engine import calibrated


TEST_PATH_GLOBS = (
    "**/test_*.py",
    "**/*_test.py",
    "**/conftest.py",
    "**/tests/**",
    "**/__tests__/**",
    "**/*.test.*",
    "**/*.spec.*",
)


class EngineConfig(BaseModel):
    """Unknown keys are an error, so a typo in a --config file can't be silently ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # master rules
    line_threshold: int = 800  # §4.1, effective lines
    async_boundary_threshold: int = 3  # §4.1
    # §4.2 / §10.4: fitted by `--fit-thresholds` and stored in calibrated.py, not hard-coded.
    semantic_path_flag_threshold: float = calibrated.SEMANTIC_PATH_FLAG_THRESHOLD
    nesting_flag_threshold: float = calibrated.NESTING_FLAG_THRESHOLD
    rating_mode: Literal["master", "nesting"] = calibrated.RATING_MODE
    fragmentation_avg_import_threshold: float = 4
    fragmentation_min_files: int = 3
    fragmentation_flat_penalty: int = 25
    coupling_weight_threshold: int = 12  # distinct files depended on + depending on this one
    coupling_min_lines: int = 40  # effective lines; a tiny barrel file is not a hot spot
    wrapper_max_branches: int = 1  # a named function with at most this many decisions ...
    wrapper_max_lines: int = 4  # ... and at most this many effective lines counts as a trivial wrapper
    wrapper_min_functions: int = 6  # smaller files are never taxed
    wrapper_free_density: float = 0.6  # share of trivial wrappers a file may have for free (about p90 of human code)
    wrapper_tax_max_points: float = 15  # purity points a file made only of wrappers loses
    namespace_min_files: int = 2  # files assigning `Name.x = ...` before Name counts as a shared global
    purity_score_fail_threshold: int = 40  # §4.5
    # review fixes
    effective_line_width: int = 40  # A4, Oct 2026 amendment (was 100)
    max_error_coverage: float = 0.05  # C2
    max_file_bytes: int = 1_000_000  # C4
    max_line_chars: int = 5000  # a longer line (minified code, one-line data) is not parsed: range() is quadratic on it
    lazy_children_threshold: int = 512  # a node with more children is walked one child at a time (constant memory)
    max_children: int = 50_000  # one node with more children is generated data: materializing them costs ~2 KB each
    score_cap: int = 1_000_000  # B3
    volatility_window_commits: int = 200  # --order-by-volatility reads this many recent commits
    count_optional_calls: bool = False  # A2
    control_flow_booleans: bool = True  # A7; parity runs turn it off (§12.3)
    vendored_path_globs: tuple[str, ...] = (
        "**/node_modules/**",
        "**/dist/**",
        "**/build/**",
        "**/.next/**",
        "**/coverage/**",
        "**/vendor/**",
    )
    generated_path_globs: tuple[str, ...] = (
        "**/*.d.ts",
        "**/__generated__/**",
        "**/*_pb2.py",
        "**/*_pb2_grpc.py",
        "**/*.generated.*",
    )
    generated_markers: tuple[str, ...] = ("@generated", "DO NOT EDIT", "auto-generated")
    test_path_globs: tuple[str, ...] = TEST_PATH_GLOBS  # §6.1
    python_source_roots: tuple[str, ...] = (".", "src")
    ts_path_aliases: dict[str, list[str]] = {}  # filled by the CLI from tsconfig
    ts_base_url: str | None = None
    # async boundaries (§9.2)
    async_listener_methods: tuple[str, ...] = (
        "on",
        "once",
        "addListener",
        "addEventListener",
        "subscribe",
        "consume",
        "process",
        "onSnapshot",
    )
    async_constructors: tuple[str, ...] = ("WebSocket", "WebSocketServer", "EventSource", "Worker")
    py_async_listener_methods: tuple[str, ...] = (
        "subscribe",
        "consume",
        "basic_consume",
        "on",
        "add_listener",
        "add_event_handler",
    )
    py_async_decorators: tuple[str, ...] = ("task", "shared_task", "websocket", "on_event", "subscriber")
    py_async_constructors: tuple[str, ...] = (
        "KafkaConsumer",
        "AIOKafkaConsumer",
        "Consumer",
        "WebSocketApp",
    )

    @property
    def active_threshold(self) -> float:
        """The flag threshold for the active rating_mode."""
        if self.rating_mode == "nesting":
            return self.nesting_flag_threshold
        return self.semantic_path_flag_threshold


DEFAULT_CONFIG = EngineConfig()


def matches_any(path: str, globs: tuple[str, ...]) -> bool:
    """Glob match on a POSIX path; `**/` also matches zero folders, `*` never crosses `/`."""
    return any(_glob_regex(glob).fullmatch(path) for glob in globs)


@lru_cache(maxsize=256)
def _glob_regex(glob: str) -> re.Pattern[str]:
    """Compile one glob (cached): `**/` spans folders, `*` and `?` stay in one."""
    pattern = re.escape(glob)
    pattern = pattern.replace(r"\*\*/", "(?:.*/)?").replace(r"/\*\*", "(?:/.*)?")
    pattern = pattern.replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.compile(pattern)
