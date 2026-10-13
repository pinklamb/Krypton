"""Measuring helpers for the stress tests: metrics per source, CLI capture, memory and timing."""

from __future__ import annotations

import contextlib
import gc
import io
import sys
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path

from krypton import cli
from krypton.engine.ast_counters import analyze_tree
from krypton.engine.config import DEFAULT_CONFIG, EngineConfig
from krypton.engine.languages import parse_source
from krypton.engine.run import run_local


@dataclass(frozen=True)
class Measured:
    """What one source file scores, in both rating modes."""

    label: str
    physical_lines: int
    characters: int
    effective_lines: int
    branches: int
    raw_outputs: int
    effective_outputs: int
    nesting_score: int
    max_depth: int
    rating_master: float
    rating_nesting: float
    flagged: bool
    flag_reason: str | None
    purity: int


def measure(label: str, source: str, filename: str, cfg: EngineConfig = DEFAULT_CONFIG) -> Measured:
    """Run the real engine on one file and keep the numbers the stress tests compare."""
    result = run_local({filename: source.encode()}, cfg=cfg)
    finding = result.findings[0]
    worst = max(finding.functions, key=lambda fn: fn.rating_master, default=None)
    return Measured(
        label=label,
        physical_lines=finding.line_count,
        characters=len(source),
        effective_lines=finding.effective_lines,
        branches=finding.cyclomatic_branches,
        raw_outputs=worst.determinate_outputs if worst else 0,
        effective_outputs=worst.outputs_effective if worst else 0,
        nesting_score=finding.nesting_score,
        max_depth=finding.max_depth,
        rating_master=max((fn.rating_master for fn in finding.functions), default=0.0),
        rating_nesting=max((fn.rating_nesting for fn in finding.functions), default=0.0),
        flagged=finding.flagged,
        flag_reason=finding.flag_reason,
        purity=result.purity_score,
    )


def tree_metrics(source: str, filename: str):
    """The raw AstMetrics, for fields FileFinding doesn't carry (module saturation)."""
    return analyze_tree(parse_source(filename, source.encode()), DEFAULT_CONFIG, filename)


def write_samples(root: Path, files: dict[str, str]) -> Path:
    """Write sample files under root (replacing it) and return the folder."""
    for old in sorted(root.rglob("*"), reverse=True) if root.exists() else []:
        old.unlink() if old.is_file() else old.rmdir()
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def run_cli(args: list[str]) -> tuple[str, int]:
    """The CLI's real output (stdout, then any warnings) and exit code, as it would print."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(args)
    text = out.getvalue().rstrip()
    warnings = err.getvalue().strip()
    return (text + ("\n\n[stderr]\n" + warnings if warnings else ""), code)


def working_set_mb() -> float | None:
    """This process's resident memory (includes the Rust parser's), or None if unavailable."""
    return _memory_mb(peak=False)


def peak_working_set_mb() -> float | None:
    """The highest resident memory this process has reached so far, or None if unavailable."""
    return _memory_mb(peak=True)


def _memory_mb(peak: bool) -> float | None:
    try:
        import psutil

        info = psutil.Process().memory_info()
        return (getattr(info, "peak_wset", info.rss) if peak else info.rss) / 1_048_576
    except ImportError:
        return _windows_memory_mb(peak) if sys.platform == "win32" else None


def _windows_memory_mb(peak: bool) -> float | None:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in ("Peak", "WorkingSetSize", "QPP", "QP", "QPNP", "QNP", "Pagefile", "PeakPagefile")
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return (counters.Peak if peak else counters.WorkingSetSize) / 1_048_576


@dataclass(frozen=True)
class Scaling:
    levels: int
    seconds: float
    peak_python_mb: float
    working_set_delta_mb: float | None
    nesting_score: int | None
    error: str | None = None


def scaling_run(levels: int, source: str) -> Scaling:
    """Time one analysis and record peak Python allocations and the change in resident memory."""
    gc.collect()
    before = working_set_mb()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        score = tree_metrics(source, "deep.js").nesting_score
        error = None
    except Exception as exc:  # a failure is a result here, not a crash
        score, error = None, f"{type(exc).__name__}: {exc}"
    seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    after = working_set_mb()
    delta = None if before is None or after is None else after - before
    return Scaling(levels, seconds, peak / 1_048_576, delta, score, error)


def leak_check(source: str, filename: str, iterations: int, checkpoints: list[int]) -> list[tuple[int, float | None, float]]:
    """Analyze the same file repeatedly; (iteration, resident MB, Python-tracked MB) at checkpoints."""
    rows = []
    tracemalloc.start()
    for iteration in range(1, iterations + 1):
        tree_metrics(source, filename)
        if iteration in checkpoints:
            gc.collect()
            current, _ = tracemalloc.get_traced_memory()
            rows.append((iteration, working_set_mb(), current / 1_048_576))
    tracemalloc.stop()
    return rows
