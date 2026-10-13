"""Spec §12.3: JS/TS flat counts match ESLint's complexity rule (minus default params and ?.).

Needs Node and `npm install` in tests/parity/eslint; run with `python -m pytest -m parity`.
The corpus is every JS/TS source in that install (ESLint, typescript-eslint, rxjs, zod, ...).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from krypton.engine.ast_counters import analyze_tree
from krypton.engine.languages import parse_source
from tests.parity.test_radon_parity import PARITY_CONFIG

ESLINT = Path(__file__).parent / "eslint"
SOURCES = (".js", ".cjs", ".mjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")
BATCH = 200


def _corpus() -> list[Path]:
    paths = []
    for path in sorted((ESLINT / "node_modules").rglob("*")):
        is_declaration = path.name.endswith((".d.ts", ".d.mts", ".d.cts"))
        if path.suffix in SOURCES and not is_declaration and path.is_file() and path.stat().st_size < 3_000_000:
            paths.append(path)
    return paths


def _eslint_counts(paths: list[Path]) -> dict[str, str]:
    counts = {}
    for start in range(0, len(paths), BATCH):
        names = [path.relative_to(ESLINT).as_posix() for path in paths[start : start + BATCH]]
        completed = subprocess.run(
            ["node", "count.mjs", *names], cwd=ESLINT, capture_output=True, text=True, encoding="utf-8"
        )
        for line in completed.stdout.splitlines():
            name, _raw, adjusted = line.split("\t")
            counts[name] = adjusted
    return counts


@pytest.mark.parity
def test_js_ts_flat_counts_match_eslint():
    if shutil.which("node") is None or not (ESLINT / "node_modules" / "eslint").is_dir():
        pytest.skip("needs Node and `npm install` in tests/parity/eslint")
    corpus = _corpus()
    expected = _eslint_counts(corpus)
    compared, mismatches = 0, []
    for path in corpus:
        name = path.relative_to(ESLINT).as_posix()
        reference = expected.get(name, "PARSE_ERROR")
        parsed = parse_source(path.name, path.read_bytes())
        if reference == "PARSE_ERROR" or parsed.has_error:
            continue
        compared += 1
        got = analyze_tree(parsed, PARITY_CONFIG).cyclomatic_branches
        if got != int(reference):
            mismatches.append(f"{name}: krypton={got} eslint={reference}")

    print(f"compared={compared} mismatched={len(mismatches)}")
    assert compared > 2_000
    assert mismatches == [], "\n".join(mismatches[:25])
