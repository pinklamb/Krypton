"""Score source text with the Rust engine and print a small JSON summary.

Reads {path: text} as JSON on stdin. The repository is never imported, compiled or run, and no repository file exists on disk:
`krypton wire --stdin-json` parses the text in memory into a syntax tree (names and strings stay in this process), and only the tree is
posted to the Krypton scoring server (KRYPTON_SCORE_URL). Runs as its own process so a hard repository can be stopped by a timeout and a memory
limit without touching the web server.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

CHARS_PER_TOKEN = 3.5  # a rough estimate, the same the engine uses for its file manifest
WORST = 8
EXAMPLES = 6
EXCLUDED_REASONS = ("vendored_path", "vendored_name", "vendored_content", "generated_path")   # files the engine left out as libraries or generated code
DIGEST = re.compile(r"[0-9a-f]{16}")  # the server sees names only as these
WALK_SECONDS = 40
SCORE_SECONDS = 50


def limit_resources() -> None:
    """Cap memory and CPU time where the platform allows it."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    except (ImportError, ValueError, OSError):
        pass


def tangled_functions(result: dict) -> list[tuple[float, str, str, int]]:
    """(times over the limit, file, function, first line) for every function at or over it, worst first. The server says how many times over."""
    over = [(fn["times_over_limit"], file["filename"], fn["name"], fn["start_line"]) for file in result["files"] for fn in file["functions"] if fn["times_over_limit"] is not None]
    return sorted(over, reverse=True)


def worst_rows(tangled: list[tuple[float, str, str, int]]) -> list[dict]:
    """The few worst functions, as the page shows them."""
    return [{"times": round(times, 1), "file": name, "function": function, "line": line} for times, name, function, line in tangled[:WORST]]


def totals(result: dict) -> dict:
    """Counts straight from the engine's result."""
    return {
        "files": len(result["files"]),
        "skipped": len(result["skipped_files"]),
        "lines": sum(file["line_count"] for file in result["files"]),
        "functions": sum(len(file["functions"]) for file in result["files"]),
        "purity": result["purity_score"],
        "flagged_files": result["flagged_file_count"],
        "unparseable": sum(1 for file in result["files"] if file["parse_error"]),
        "languages": dict(Counter(file["language"] for file in result["files"])),
    }


def excluded(result: dict) -> dict:
    """How many files were left out as bundled libraries or generated code, with a few example paths."""
    names = sorted(skip["filename"] for skip in result["skipped_files"] if skip["reason"] in EXCLUDED_REASONS)
    return {"count": len(names), "examples": names[:EXAMPLES]}


def summarize(result: dict, sources: dict[str, str]) -> dict:
    """The numbers the page shows; no source text."""
    tangled = tangled_functions(result)
    characters = sum(len(sources[file["filename"]]) for file in result["files"])
    return {**totals(result), "tokens": round(characters / CHARS_PER_TOKEN), "tangled": len(tangled), "worst": worst_rows(tangled), "excluded": excluded(result)}


def wire_body(sources: dict[str, str], names_file: Path) -> bytes:
    """The request body for the scoring server: the tree of every file, built here. Real names go to `names_file`, never to the server."""
    walk = os.environ.get("KRYPTON_BIN", "krypton")
    done = subprocess.run([walk, "wire", "--stdin-json", "--names-out", str(names_file)], input=json.dumps(sources).encode(), capture_output=True, timeout=WALK_SECONDS, check=False)
    if done.returncode != 0:
        raise SystemExit(f"krypton failed ({done.returncode}): {done.stderr.decode(errors='replace')[-400:]}")
    return done.stdout


def ask_server(body: bytes) -> dict:
    """POST the tree to the scoring server and return its `result`."""
    url = os.environ.get("KRYPTON_SCORE_URL", "http://127.0.0.1:8081")
    headers = {"Content-Type": "application/json", "Content-Encoding": "gzip", "Authorization": f"Bearer {os.environ.get('KRYPTON_SERVICE_TOKEN', '')}"}
    request = urllib.request.Request(f"{url}/v1/score", data=gzip.compress(body, 1), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=SCORE_SECONDS) as reply:
            return json.loads(reply.read())["result"]
    except urllib.error.HTTPError as error:
        raise SystemExit(f"the scoring server refused the tree ({error.code}): {error.read().decode(errors='replace')[:400]}") from error
    except (urllib.error.URLError, OSError) as error:
        raise SystemExit(f"cannot reach the scoring server: {error}") from error


def put_names_back(summary: dict, names: dict[str, str]) -> dict:
    """The worst functions as the visitor wrote them (the server only ever saw digests)."""
    for row in summary["worst"]:
        row["function"] = DIGEST.sub(lambda found: names.get(found.group(), found.group()), row["function"])
    return summary


CODES = "123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
NODE_BUDGET = 30000  # enough points to see the shape; a bigger repository shows the first part of each file's tree


def marks_for(tree: dict, path: str, tangled: list) -> list[str]:
    """For every node of one file, the code of the tangled function it belongs to ("0" for none). Earlier (worse) functions win."""
    marks = ["0"] * tree["n"]
    for q, (_, where, fn) in enumerate(tangled):
        if where != path:
            continue
        for i, start in enumerate(tree["start_line"]):
            if marks[i] == "0" and fn["start_line"] <= start + 1 <= fn["end_line"]:
                marks[i] = CODES[q]
    return marks


def kept_nodes(parent: list[int], marks: list[str], share: float) -> list[int]:
    """The nodes to draw, in their original order: the first share of the file, every node of a tangled function, and each such node's
    ancestors, so a tangled function is always drawn whole and attached to its file however big the repository is."""
    prefix = len(parent) if share >= 1 else max(1, math.ceil(len(parent) * share))
    keep = set(range(prefix))
    for i, mark in enumerate(marks):
        while mark != "0" and i >= 0 and i not in keep:
            keep.add(i)
            i = parent[i]
    return sorted(keep)


def outline(body: dict, result: dict, names: dict[str, str]) -> dict:
    """The tree for the page to draw (the shape the landing page draws garth from): per file its parent array and which nodes belong to a
    tangled function, the import arcs, and the tangled functions by real name. Numbers and names only, no source text."""
    files = body["files"]
    total = sum(f["tree"]["n"] for f in files) or 1
    share = min(1.0, NODE_BUDGET / total)
    tangled = sorted(((fn["times_over_limit"], rf["filename"], fn) for rf in result["files"] for fn in rf["functions"] if fn["times_over_limit"] is not None),
                     key=lambda row: -row[0])[: len(CODES)]
    index = {f["path"]: k for k, f in enumerate(files)}
    shaped = []
    for f in files:
        tree = f["tree"]
        marks = marks_for(tree, f["path"], tangled)
        keep = kept_nodes(tree["parent"], marks, share)
        renumber = {old: new for new, old in enumerate(keep)}
        shaped.append({"p": f["path"], "t": [renumber.get(tree["parent"][old], -1) for old in keep], "h": "".join(marks[old] for old in keep)})
    edges = sorted({(a, index[path]) for a, f in enumerate(files) for group in f.get("imports", []) for path in group if path in index and index[path] != a})
    fns = [{"name": DIGEST.sub(lambda found: names.get(found.group(), found.group()), fn["name"]), "times": round(times, 1), "file": path} for times, path, fn in tangled]
    return {"files": shaped, "edges": [list(e) for e in edges], "fns": fns}


def main() -> int:
    """stdin JSON in, summary JSON out."""
    limit_resources()
    sources = json.load(sys.stdin)
    scored = {name: text for name, text in sources.items() if name != ".gitattributes"}
    with tempfile.TemporaryDirectory() as work:
        names_file = Path(work) / "names.json"
        body = wire_body(sources, names_file)
        result = ask_server(body)
        names = json.loads(names_file.read_text(encoding="utf-8")) if names_file.exists() else {}
    summary = put_names_back(summarize(result, scored), names)
    summary["tree"] = outline(json.loads(body), result, names)
    json.dump(summary, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
