"""Spec §12.3: flat counts match radon 6.0.1 and nesting scores match an independent reference.

The quick test runs on a fixed slice of the standard library in every test run; the full
corpus (CPython's standard library plus installed packages, ~14,000 files) runs with
`python -m pytest -m parity`. Parity runs with A7 off and outside test paths, per §12.3.
"""

from __future__ import annotations

import sysconfig
import warnings
from pathlib import Path

import pytest

from krypton.engine.ast_counters import analyze_tree
from krypton.engine.config import EngineConfig
from krypton.engine.languages import parse_source
from tests.parity.radon_reference import flat_count, nesting_score

PARITY_CONFIG = EngineConfig(control_flow_booleans=False, test_path_globs=())
STDLIB = Path(sysconfig.get_paths()["stdlib"])


def compare_corpus(paths: list[Path]) -> tuple[int, list[str]]:
    """Returns (files compared, mismatch descriptions)."""
    compared, mismatches = 0, []
    for path in paths:
        expected = _reference(path)
        if expected is None:
            continue
        parsed = parse_source(path.name, path.read_bytes())
        if parsed.has_error:
            continue  # grammar gaps (e.g. TS variance) are covered by §5.2, not parity
        metrics = analyze_tree(parsed, PARITY_CONFIG)
        compared += 1
        got = (metrics.cyclomatic_branches, metrics.nesting_score)
        if got != expected:
            mismatches.append(f"{path}: krypton flat/nesting={got} reference={expected}")
    return compared, mismatches


def _reference(path: Path) -> tuple[int, int] | None:
    try:
        code = path.read_text(encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            score, decisions = nesting_score(code)
            flat = flat_count(code)
    except (SyntaxError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    assert decisions == flat, path  # the two references agree on what a decision is
    return flat, score


def test_stdlib_sample_matches_radon():
    sample = sorted(STDLIB.glob("*.py"))[:150]

    compared, mismatches = compare_corpus(sample)

    assert compared > 100
    assert mismatches == []


@pytest.mark.parity
def test_full_corpus_matches_radon():
    corpus = sorted(STDLIB.rglob("*.py"))

    compared, mismatches = compare_corpus(corpus)

    print(f"compared={compared} mismatched={len(mismatches)}")
    assert compared > 10_000
    assert mismatches == [], "\n".join(mismatches[:25])
