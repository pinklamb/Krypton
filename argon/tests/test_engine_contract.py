"""Spec §3 / §12.4: engine/ does no I/O, and a 1,000-file scan finishes in under 30 seconds."""

import ast
import time
from pathlib import Path

from argon.engine.run import run_local

ENGINE = Path(__file__).parent.parent / "argon" / "engine"
FORBIDDEN = {"os", "pathlib", "subprocess", "socket", "http", "urllib", "requests", "httpx", "aiohttp"}


def test_engine_imports_no_io_modules():
    offenders = []
    for path in sorted(ENGINE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            offenders += [f"{path.name}: {module}" for module in modules if module.split(".")[0] in FORBIDDEN]

    assert offenders == []


def test_thousand_files_under_thirty_seconds():
    python = (Path(__file__).parent / "fixtures" / "spec" / "isolation_2.py").read_bytes() * 20
    javascript = (Path(__file__).parent / "fixtures" / "branches.js").read_bytes() * 20
    files = {f"src/m{index}.py": python for index in range(500)}
    files.update({f"web/m{index}.js": javascript for index in range(500)})

    started = time.perf_counter()
    result = run_local(files)
    elapsed = time.perf_counter() - started

    assert len(result.findings) == 1_000
    assert elapsed < 30, f"{elapsed:.1f}s"
