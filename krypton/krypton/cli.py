"""Command line: scan files or folders, print the report, exit 0 / 1 / 2 (spec §11).

The only module that touches disk or git; the engine gets bytes and returns models.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from pydantic import ValidationError

from krypton import history, progress, report, runlog
from krypton.calibration import calibrated_module, calibration_csv, calibration_report, fit_thresholds, read_csv
from krypton.engine.config import LEVELS, EngineConfig, apply_level
from krypton.engine.languages import SUPPORTED_EXTENSIONS
from krypton.engine.run import run_local
from krypton.engine.volatility import change_frequency, manifest

EXIT_SUCCESS, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2
CALIBRATED_MODULE = Path(__file__).parent / "engine" / "calibrated.py"
HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# A string (kept whole, so `//` inside a URL survives), or a line / block comment.
JSONC_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?(?:\*/|$)', re.DOTALL)
TRAILING_COMMA = re.compile(r",(\s*[}\]])")


class UsageError(Exception):
    """A problem with the command line or its inputs; reported as exit code 2."""

    pass


def main(argv: list[str] | None = None) -> int:
    """Run the CLI: scan, report (or --json), optional calibration; returns the exit code."""
    args = _parser().parse_args(argv)
    try:
        if args.fit_thresholds:
            return _fit_thresholds(*args.fit_thresholds)
        files, altered, cfg = _scan_inputs(args)
        frequency = _change_frequency(args, files, cfg)
    except UsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    sources = {name: path.read_bytes() for name, path in files.items()}
    result = run_local(sources, altered, cfg, frequency)
    if args.history:
        result = _log_progress(result, sources, args, cfg)
    if args.order_by_volatility:
        sizes = {name: len(data.decode("utf-8", errors="replace")) for name, data in sources.items()}
        print(json.dumps(manifest(result.findings, sizes), indent=2))
    elif args.json:
        print(result.model_dump_json(indent=2))
    else:
        _print_report(result, args.top)
    if args.calibrate or args.calibrate_out:
        _calibrate(sources, altered, cfg, args)
    return EXIT_FAILURE if result.check_conclusion == "failure" else EXIT_SUCCESS


def _scan_inputs(args) -> tuple[dict[str, Path], dict[str, set[int]] | None, EngineConfig]:
    """Files to score, their altered lines (None = all) and the config; UsageError if none."""
    if not args.paths:
        raise UsageError("give at least one file or folder to scan")
    root = _scan_root(args.paths)
    cfg = apply_level(_load_config(args.config, root), args.level)
    files = _collect_files(args.paths, root)
    altered = None
    if args.base:
        files, altered = _changed_since(args.base, root, files)
    if not files:
        raise UsageError("no supported source files found; expected one of: " + ", ".join(sorted(SUPPORTED_EXTENSIONS)))
    return files, altered, cfg


def _log_progress(result, sources: dict[str, bytes], args, cfg: EngineConfig):
    """Compare with the --history log, append this run, and return the result with `progress` filled in."""
    texts = {name: data.decode("utf-8", errors="replace") for name, data in sources.items()}
    runs = runlog.read_runs(args.history)
    snapshot = runlog.take_snapshot(result, texts, f"{'base:' + args.base if args.base else 'full'}, {args.level}")
    shown = progress.compare(snapshot, runs, runlog.next_number(runs), cfg)
    runlog.append_run(args.history, runs, snapshot.record)
    return result.model_copy(update={"progress": shown})


def _change_frequency(args, files: dict[str, Path], cfg: EngineConfig) -> dict[str, float] | None:
    """Per-file change frequency when --order-by-volatility or --volatility is given, else None."""
    if not (args.order_by_volatility or args.volatility):
        return None
    try:
        if args.volatility:
            return change_frequency(history.load_counts(args.volatility), files)
        root = _scan_root(args.paths)
        counts = history.commit_counts(root, cfg.volatility_window_commits)
        return change_frequency(counts, files, history.uncommitted(root))
    except history.HistoryError as error:
        raise UsageError(str(error)) from error


def _parser() -> argparse.ArgumentParser:
    """The argparse definition of every flag (spec §11)."""
    parser = argparse.ArgumentParser(prog="krypton", description="Krypton structural review, locally.")
    parser.add_argument("paths", nargs="*", type=Path, help="source files or folders to scan")
    parser.add_argument("--top", type=int, default=20, metavar="N", help="function ranking rows (0 = none)")
    parser.add_argument("--base", metavar="REF", help="score only files and lines changed since REF")
    parser.add_argument("--config", type=Path, metavar="FILE.toml", help="override any EngineConfig field")
    parser.add_argument("--json", action="store_true", help="print EngineResult as JSON, nothing else")
    parser.add_argument("--order-by-volatility", action="store_true", help="print a JSON manifest of files, most stable first (git history), instead of the report")
    parser.add_argument("--volatility", type=Path, metavar="FILE.json", help="path -> change count, used instead of git history")
    parser.add_argument("--level", choices=list(LEVELS), default="standard", help="how strict the flag threshold is: relaxed (3x), standard, strict (0.5x)")
    parser.add_argument("--history", type=Path, metavar="FILE.jsonl", help="log this run there and report progress since the last one (the file is created)")
    parser.add_argument("--calibrate", action="store_true", help="add the calibration report")
    parser.add_argument("--calibrate-out", type=Path, metavar="FILE.csv", help="one row per function")
    parser.add_argument(
        "--fit-thresholds",
        nargs=2,
        type=Path,
        metavar=("HUMAN.csv", "AGENTIC.csv"),
        help="fit thresholds and rating mode from two --calibrate-out files; writes engine/calibrated.py",
    )
    return parser


def _print_report(result, top: int) -> None:
    """Warnings to stderr, then file table, ranking, skips and the PR-comment preview."""
    for line in report.warnings(result):
        print(line, file=sys.stderr)
    sections = [report.file_table(result)]
    if top > 0:
        sections.append(report.function_ranking(result, top))
    hints = report.refactoring_hints(result, top)
    if hints:
        sections.append(hints)
    if result.progress:
        sections.append(report.progress_section(result))
    skipped = report.skipped_and_fragmentation(result)
    if skipped:
        sections.append(skipped)
    sections.append(result.summary_markdown.rstrip())
    print("\n\n".join(sections))


def _calibrate(sources: dict[str, bytes], altered, cfg: EngineConfig, args) -> None:
    """Re-run the scan once per rating mode; print the report and/or write the CSV."""
    by_mode = {mode: run_local(sources, altered, cfg.model_copy(update={"rating_mode": mode})) for mode in ("master", "nesting")}
    if args.calibrate:
        print("\n" + calibration_report(by_mode), file=sys.stderr if args.json else sys.stdout)
    if args.calibrate_out:
        args.calibrate_out.write_text(calibration_csv(by_mode["master"]), encoding="utf-8")


def _fit_thresholds(human_csv: Path, agentic_csv: Path) -> int:
    """--fit-thresholds: fit both thresholds from two CSVs and rewrite engine/calibrated.py."""
    try:
        human = read_csv(human_csv.read_text(encoding="utf-8"))
        agentic = read_csv(agentic_csv.read_text(encoding="utf-8"))
    except OSError as error:
        raise UsageError(f"cannot read calibration data: {error}") from error
    if not human or not agentic:
        raise UsageError("both calibration files need at least one function row")
    fits = fit_thresholds(human, agentic)
    sources = {"human": human_csv.as_posix(), "agentic": agentic_csv.as_posix()}
    module = calibrated_module(fits, sources, {"human": len(human), "agentic": len(agentic)})
    CALIBRATED_MODULE.write_text(module, encoding="utf-8")
    print(module.split('"""')[1].strip())
    print(f"\nwrote {CALIBRATED_MODULE}")
    return EXIT_SUCCESS


# --- files ---------------------------------------------------------------------------


def _scan_root(paths: list[Path]) -> Path:
    """Paths are reported relative to the first path if it's a folder, else the current folder."""
    first = paths[0]
    return (first if first.is_dir() else Path.cwd()).resolve()


def _collect_files(paths: list[Path], root: Path) -> dict[str, Path]:
    """{POSIX path relative to root: file}. Folders inherit .gitignore when inside a git repo."""
    files = {}
    for path in paths:
        files.update(_named_sources(_folder_files(path) if path.is_dir() else [path], root))
    return dict(sorted(files.items()))


def _named_sources(candidates: list[Path], root: Path) -> dict[str, Path]:
    """Supported files inside the scan root, keyed by POSIX path relative to it."""
    named = {}
    for candidate in candidates:
        name = _inside_root(candidate, root)
        if name is not None and candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
            named[name] = candidate
    return named


def _inside_root(path: Path, root: Path) -> str | None:
    """C5: files whose real path leaves the scan root (symlinks) are dropped."""
    if not path.is_file():
        return None
    real = path.resolve()
    if not real.is_relative_to(root):
        return None
    return Path(os.path.relpath(path.absolute(), root)).as_posix()


def _folder_files(folder: Path) -> list[Path]:
    """Files under a folder as git sees them (honoring .gitignore), else every file."""
    listing = _git(folder, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if listing is None:
        print(f"WARN {folder}: not a git repository, .gitignore not applied", file=sys.stderr)
        return _walk_folder(folder)
    return [folder / name for name in listing.split("\0") if name]


def _walk_folder(folder: Path) -> list[Path]:
    """Every file under folder, without following symlinks or entering .git."""
    found = []
    for directory, subdirectories, filenames in os.walk(folder, followlinks=False):
        if ".git" in subdirectories:
            subdirectories.remove(".git")  # in place, so os.walk never descends into it
        found += [Path(directory, filename) for filename in filenames]
    return found


def _git(cwd: Path, *args: str) -> str | None:
    """Run a git command in cwd; its stdout, or None if git or the repo is missing."""
    try:
        completed = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def _changed_since(base: str, root: Path, files: dict[str, Path]):
    """--base: files in `git diff -w` since REF, with their added line numbers (A5)."""
    diff = _git(
        root, "-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/",
        "-w", "--unified=0", "--relative", base, "--", ".",
    )
    if diff is None:
        raise UsageError(f"git diff against {base!r} failed; is {root} inside a git repository?")
    altered = _added_lines(diff)
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z") or ""
    for name in untracked.split("\0"):
        if name in files:  # new files count as fully altered
            altered[name] = set(range(1, files[name].read_bytes().count(b"\n") + 2))
    changed = {name: path for name, path in files.items() if name in altered}
    return changed, {name: altered[name] for name in changed}


def _added_lines(diff: str) -> dict[str, set[int]]:
    """Added line numbers per file from a `--unified=0` diff.

    A hunk header `@@ -a,b +c,d @@` says its body is b removed and d added lines, so body
    lines are consumed by count and never mistaken for headers (an added line `++ x` shows
    up as `+++ x`). File headers are only read between `diff --git` and the first hunk.
    """
    altered: dict[str, set[int]] = {}
    current: set[int] | None = None
    in_header = False
    owed = 0  # body lines of the current hunk not yet seen
    for line in diff.splitlines():
        if owed and not line.startswith("\\"):
            owed -= 1
        elif owed:
            continue  # "\ No newline at end of file" belongs to the hunk but is no line
        elif line.startswith("diff --git "):
            current, in_header = None, True
        elif in_header and line.startswith("+++ "):
            current = _diff_target(line, altered)
        elif (hunk := HUNK.match(line)) is not None:
            in_header = False
            owed = _open_hunk(hunk, current)
    return altered


def _open_hunk(hunk: re.Match[str], current: set[int] | None) -> int:
    """Record a hunk's added lines in `current`; the number of body lines that follow it."""
    removed, start, added = int(hunk.group(1) or 1), int(hunk.group(2)), int(hunk.group(3) or 1)
    if current is not None:
        current.update(range(start, start + added))
    return removed + added


def _diff_target(header: str, altered: dict[str, set[int]]) -> set[int] | None:
    """The altered-line set for a `+++ b/path` diff header; None for a deleted file."""
    target = header[4:].removesuffix(TAB)  # git appends a TAB after names containing spaces
    if target == "/dev/null":  # a deleted file
        return None
    if target.startswith('"'):
        target = _unquote(target)
    return altered.setdefault(target.removeprefix("b/"), set())


TAB = chr(9)
BACKSLASH = chr(92)
ESCAPES = {"n": b"\n", "t": b"\t", "r": b"\r", "a": b"\a", "b": b"\b", "f": b"\f", "v": b"\v", '"': b'"', BACKSLASH: b"\\"}


def _unquote(quoted: str) -> str:
    """Undo git's C-style path quoting: backslash escapes and three-digit octal UTF-8 bytes."""
    raw, body, index = bytearray(), quoted[1:-1], 0
    while index < len(body):
        char, octal = body[index], body[index + 1 : index + 4]
        if char != BACKSLASH:
            raw += char.encode()
            index += 1
        elif len(octal) == 3 and octal.isdigit():
            raw.append(int(octal, 8))
            index += 4
        else:
            raw += ESCAPES.get(body[index + 1 : index + 2], b"\\")
            index += 2
    return raw.decode("utf-8", errors="replace")


# --- config --------------------------------------------------------------------------


def _load_config(config_file: Path | None, root: Path) -> EngineConfig:
    """EngineConfig from --config TOML; tsconfig path aliases fill fields it leaves unset."""
    overrides = {}
    if config_file is not None:
        try:
            overrides = tomllib.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise UsageError(f"cannot read {config_file}: {error}") from error
    for name, value in _tsconfig_paths(root).items():
        overrides.setdefault(name, value)  # an explicit --config value wins
    try:
        return EngineConfig(**overrides)
    except ValidationError as error:
        raise UsageError(f"invalid config: {error}") from error


def _tsconfig_paths(root: Path) -> dict:
    """A8: compilerOptions.paths and baseUrl from tsconfig.json / jsconfig.json (JSONC)."""
    found = [root / name for name in ("tsconfig.json", "jsconfig.json") if (root / name).is_file()]
    if not found:
        return {}
    options = _parse_jsonc(found[0].read_text(encoding="utf-8")).get("compilerOptions", {})
    settings = {"ts_path_aliases": dict(options.get("paths", {}))}
    if options.get("baseUrl") is not None:
        settings["ts_base_url"] = Path(options["baseUrl"]).as_posix()
    return settings


def _parse_jsonc(text: str) -> dict:
    """JSON with // and /* */ comments and trailing commas, as tsconfig allows."""
    without_comments = JSONC_TOKEN.sub(lambda match: match.group(0) if match.group(0).startswith('"') else "", text)
    try:
        parsed = json.loads(TRAILING_COMMA.sub(r"\1", without_comments))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
