"""Is it cheaper to keep changing code that Krypton has cleaned up, or to run Krypton while the agent works? Measure it with real Claude agents.

    python -m tools.agent_cost --dry                                  # plan only
    python -m tools.agent_cost --arms A --tasks 1 --max-usd 3 --yes   # a first live run
    python -m tools.agent_cost --max-usd 17.5 --yes                   # the full experiment

Three arms start from the same copy of garth and work through the same three change tickets, one agent run per ticket
(each a fresh conversation on the code the previous ticket left behind):

  A  the original code, no Krypton anywhere
  B  the original code refactored once up front by an agent with Krypton, then the tickets with no Krypton
  C  the original code, with Krypton in the loop: the agent runs it after each change and fixes what it flags

The agent has read, search, edit and test tools only (plus Krypton in the refactor stage and in arm C). Every request's
`usage` is saved, so tokens, cache reads and writes, and dollars are measured, not estimated. After each ticket, hidden
acceptance tests, garth's own test suite and Krypton's score for the code decide whether the work was correct.
The key is read from the environment or .env and is never passed to the tools' subprocesses.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from tools.cache_anthropic import MODEL, PRICES, Budget, BudgetExceeded, find_key, request_cost

MAX_TOKENS = 8192
ROOT = Path(__file__).resolve().parents[2]
KRYPTON_SRC = ROOT / "krypton"
IGNORED = ("docs", "colabs", ".github", ".vscode", ".devcontainer", ".git", "__pycache__", ".pytest_cache", "htmlcov")
TICKETS = {
    1: """Ticket 1: harden log redaction (src/garth/telemetry.py)
`sanitize` and `sanitize_headers` leak secrets in some cases. Fix them so that:
- in plain text and URLs, query-string style values for `client_secret`, `access_token` and `password` are replaced with `[REDACTED]` (for example `https://x/y?client_secret=abc&q=1` becomes `https://x/y?client_secret=[REDACTED]&q=1`, and `password=hunter2` becomes `password=[REDACTED]`);
- `sanitize_headers` also hides `X-Api-Key` and `Proxy-Authorization`, whatever their capitalisation, besides what it hides today;
- in JSON text, sensitive fields are redacted at any depth inside nested objects and lists, and also when the JSON text is a list at the top level;
- `client_secret` and `id_token` count as sensitive JSON fields too, alongside the existing ones;
- text that is not JSON, and JSON that is neither an object nor a list (such as `[1, 2, 3]` or a bare number), comes back unchanged.
Keep everything that works today working.""",
    2: """Ticket 2: body battery events (src/garth/data/body_battery/events.py)
`BodyBatteryData.get` needs these changes to how it parses events:
- every event gets an `event_end_time_gmt` field: its start time plus its duration, as a timezone-aware UTC datetime (the start time field stays `event_start_time_gmt`);
- a negative duration is treated as 0, so the end time then equals the start time;
- an item whose event has no start time is skipped, an item with no `event` at all is kept with `event` set to None and its other fields (such as `activity_name`) intact;
- if the response is not a list, return an empty list.
All other fields keep parsing exactly as they do today.""",
    3: """Ticket 3: HTTP client headers (src/garth/http.py)
Bug: request headers set for one call leak into later calls on the same client. For example after `client.request("GET", "connect", "/b", referrer=True)` the `referer` header is still sent on `client.request("GET", "connect", "/c")`.
Fix that, and add a feature: `client.configure(default_headers={...})` sets headers sent with every request. A request's own `headers=` override the defaults per key. Requirements:
- the default headers dict itself is never changed by making requests and survives later `configure(...)` calls (for example `configure(timeout=5)`);
- the dict a caller passes as `headers=` is never mutated;
- headers added for one request (such as `referer`) apply to that request only.""",
    4: """Ticket 4: key conversion helpers (src/garth/utils.py)
`camel_to_snake_dict` and `remove_dto_suffix_from_dict` need fixing:
- both must also handle lists nested inside lists, at any depth (for example `{"outerKey": [[{"innerKey": 1}], [2]]}`), not only lists of dictionaries;
- keys that are not strings (for example integers) are kept as they are instead of raising an error;
- `remove_dto_suffix` removes a `DTO` suffix as well as `_dto` (so `summaryDTO` becomes `summary`), but a key that is only `dto` stays as it is;
- the dictionaries passed in are never modified, and plain values pass through unchanged.
Everything that works today keeps working.""",
    5: """Ticket 5: daily training status (src/garth/stats/training_status/daily.py)
`DailyTrainingStatus._parse_response` only returns the first device's data, and it edits the response it is given. Change it so that:
- it returns one entry per device in the response, in order, each with the `acuteTrainingLoadDTO` values flattened into the entry (as it does now for the first device);
- the response passed in is not modified;
- devices whose data is not a dictionary are skipped, and a device with no `acuteTrainingLoadDTO` is returned as it is;
- a response of the wrong shape (not a dictionary, or with missing or malformed sections) gives an empty list.""",
    6: """Ticket 6: stats paging (src/garth/stats/_base.py)
`Stats.list` fetches long periods one page at a time. Fix it so that:
- if the newest page has no data, the older pages are still fetched and returned (today the whole result is dropped);
- each page is requested exactly once and the results come back oldest first;
- if the API returns the same calendar date more than once, only the last one is kept;
- a `period` below 1 raises `ValueError`.
Existing behaviour for normal periods stays the same.""",
    7: """Ticket 7: body battery events, filters (src/garth/data/body_battery/events.py)
`BodyBatteryData.get(day, *, client=None)` gets optional filters: `get(day, *, client=None, event_types=None, min_impact=None)`.
- `event_types`: a list or tuple of strings. Keep only items whose event has an `event_type` in it. An empty list matches nothing.
- `min_impact`: a number. Keep only items whose event has `abs(body_battery_impact) >= min_impact`.
- When both are given, an item must pass both. When any filter is given, an item with no `event` is dropped.
- With no filters (the default) the result is exactly what it is today, in the same order.""",
    8: """Ticket 8: body battery events, ordering and limit (src/garth/data/body_battery/events.py)
Add two more optional keyword arguments to `BodyBatteryData.get`: `order=None` and `limit=None`.
- `order` is None (keep the response order), "asc" or "desc" (sort by `event.event_start_time_gmt`). Items with no `event` always go last, whichever order. The sort is stable. Any other value raises `ValueError`.
- `limit` is None or an int >= 0: keep only the first `limit` items after filtering and ordering (0 gives an empty list). A negative limit raises `ValueError`.""",
    9: """Ticket 9: body battery events, duplicates (src/garth/data/body_battery/events.py)
Add an optional keyword argument `dedupe=False` to `BodyBatteryData.get`. When True, of several items that have the same non-None `activity_id` only the first is kept (items with no `event` count too). Items whose `activity_id` is None are never removed. The order of operations is: parse, filters, dedupe, order, limit.""",
    10: """Ticket 10: body battery events, summary (src/garth/data/body_battery/events.py)
Add a classmethod `BodyBatteryData.summary(day=None, *, client=None, **options)` that accepts exactly the same optional keyword arguments as `get` (`event_types`, `min_impact`, `order`, `limit`, `dedupe`) and applies them the same way, then returns a dict with:
- `"count"`: how many items remain (including items with no event);
- `"total_impact"`: the sum of `body_battery_impact` over items that have an event;
- `"total_duration_ms"`: the sum of `duration_in_milliseconds` over items that have an event;
- `"by_type"`: a dict of event_type to how many items have it.
An empty or non-list response gives count 0, both totals 0 and an empty by_type.""",
    11: """Ticket 11: body battery events, duration filter and averages (src/garth/data/body_battery/events.py)
- Add an optional keyword argument `min_duration_ms=None` to both `get` and `summary`: keep only items whose event has `duration_in_milliseconds >= min_duration_ms`. It combines with the other filters like they combine with each other (an item must pass all, and an item with no event is dropped when any filter is given).
- `summary` also returns `"avg_impact_by_type"`: a dict of event_type to the mean `body_battery_impact` of the items of that type, rounded to 2 decimals. Items with no event are ignored. An empty result gives an empty dict.""",
    12: """Ticket 12: body battery events, maximum duration (src/garth/data/body_battery/events.py)
Add an optional keyword argument `max_duration_ms=None` to both `BodyBatteryData.get` and `BodyBatteryData.summary`: keep only items whose event has `duration_in_milliseconds <= max_duration_ms`. It is a filter like the others (an item must pass every filter given, and an item with no event is dropped when any filter is given).""",
    13: """Ticket 13: body battery events, maximum impact (src/garth/data/body_battery/events.py)
Add an optional keyword argument `max_impact=None` to both `get` and `summary`: keep only items whose event has `abs(body_battery_impact) <= max_impact`. It combines with `min_impact` and the other filters the same way as before.""",
    14: """Ticket 14: body battery events, excluded types (src/garth/data/body_battery/events.py)
Add an optional keyword argument `exclude_types=None` (a list or tuple of strings) to both `get` and `summary`: drop items whose event has an `event_type` in it. An empty list excludes nothing, but giving it still counts as giving a filter, so items with no event are dropped.""",
    15: """Ticket 15: body battery events, time window (src/garth/data/body_battery/events.py)
Add optional keyword arguments `since=None` and `until=None` to both `get` and `summary`. Each is a `datetime` or an ISO 8601 string such as "2024-01-01T10:00:00Z" (a trailing Z means UTC; a naive datetime is taken as UTC). Keep only items whose event start time is `>= since` and `<= until` (inclusive); either may be None. A string that is not a valid ISO time raises `ValueError`.""",
    16: """Ticket 16: body battery events, feedback types (src/garth/data/body_battery/events.py)
Add an optional keyword argument `feedback_types=None` (list or tuple of strings) to both `get` and `summary`: keep only items whose event has a `feedback_type` in it.""",
    17: """Ticket 17: body battery events, activity types (src/garth/data/body_battery/events.py)
Add an optional keyword argument `activity_types=None` (list or tuple of strings) to both `get` and `summary`: keep only items whose `activity_type` is in it. An item whose `activity_type` is None never matches. Like every filter, giving it drops items that have no event.""",
    18: """Ticket 18: body battery events, what to order by (src/garth/data/body_battery/events.py)
Add an optional keyword argument `order_by="start"` to both `get` and `summary`. It chooses what `order` sorts by: "start" (`event_start_time_gmt`, as today), "impact" (`body_battery_impact`, signed) or "duration" (`duration_in_milliseconds`). Items with no event still go last and the sort is still stable. Any other value raises `ValueError`, even when `order` is None.""",
    19: """Ticket 19: body battery events, offset (src/garth/data/body_battery/events.py)
Add an optional keyword argument `offset=0` to both `get` and `summary`: skip the first `offset` items after ordering and before `limit` is applied. The order of operations is now: parse, filters, dedupe, order, offset, limit. A negative offset raises `ValueError`.""",
    20: """Ticket 20: body battery events, average duration by type (src/garth/data/body_battery/events.py)
`BodyBatteryData.summary` also returns `"avg_duration_ms_by_type"`: a dict of event_type to the mean `duration_in_milliseconds` of the items of that type, rounded to 2 decimals. Items with no event are ignored. An empty result gives an empty dict.""",
    21: """Ticket 21: body battery events, impact range (src/garth/data/body_battery/events.py)
`summary` also returns `"max_impact"` and `"min_impact"`: the largest and smallest (signed) `body_battery_impact` over the items that have an event, or None for both when there are none.""",
    22: """Ticket 22: body battery events, events by hour (src/garth/data/body_battery/events.py)
`summary` also returns `"by_hour"`: a dict of hour (an int 0 to 23, the UTC hour of `event_start_time_gmt`) to how many items start in that hour. Items with no event are ignored. An empty result gives an empty dict.""",
    23: """Ticket 23: body battery events, busiest type (src/garth/data/body_battery/events.py)
`summary` also returns `"busiest_type"`: the `event_type` with the most items. When types tie, the one that sorts first alphabetically wins. It is None when no item has an event.""",
    24: """Ticket 24: body battery events, keep items without an event (src/garth/data/body_battery/events.py)
Add an optional keyword argument `include_eventless=False` to both `get` and `summary`. When True, items that have no event bypass every filter and are kept (they are still handled by dedupe, order, offset and limit like any item). When False, behaviour is as today: they are dropped whenever any filter is given.""",
    25: """Ticket 25: body battery events, what to de-duplicate by (src/garth/data/body_battery/events.py)
Add an optional keyword argument `dedupe_by="activity_id"` to both `get` and `summary`. It chooses what `dedupe=True` compares: "activity_id" (as today) or "start_time" (the event start time: of several items with the same start time only the first is kept; items with no event are never removed in this mode). Any other value raises `ValueError`, even when `dedupe` is False.""",
}
BASE_PROMPT = """You are a coding agent working in a Python repository (the garth library, source under src/garth, tests under tests). You are given one ticket. Make the change with the tools you have, run the existing tests to make sure nothing broke, and when you are done reply with DONE and a one-line summary. Keep changes focused. Work efficiently: read only what you need."""
KRYPTON_NEUTRAL = "\nYou have a run_krypton tool. Use Krypton while you work on this ticket."
KRYPTON_RULES = """

## Code structure (Krypton)
- After editing source files, run the run_krypton tool on the files or folders you changed.
- If a file is flagged, or a function you wrote appears with a `nested` flag, refactor it before finishing: early returns, small named helpers, one job per function.
- Do not satisfy Krypton by packing code onto fewer lines or by splitting one function into many trivial wrappers.
- Re-run Krypton after refactoring and mention the purity score in your summary."""
REFACTOR_TICKET = """Refactor ticket: make the code easier to read and change without changing behavior.
Run the run_krypton tool on src to see which functions and files are too heavy. Split the flagged functions into small named helpers (early returns, one job per function), flatten deep nesting, and run Krypton again until no function is flagged and none is marked nested. Public names, signatures and behavior must stay the same, and every existing test must keep passing. Hub files that Krypton flags only for coupling may be left as they are. When done reply with DONE and the final purity score."""

TOOLS = [
    {"name": "list_files", "description": "List the files and folders in a folder of the repository (one level).", "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Folder, default '.'"}}, "required": []}},
    {"name": "read_file", "description": "Read a text file with line numbers. Use start_line and end_line to read part of a long file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}},
    {"name": "grep", "description": "Search the repository's text files for a regular expression; returns file:line: text.", "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string", "description": "Folder or file, default '.'"}}, "required": ["pattern"]}},
    {"name": "edit_file", "description": "Replace exactly one occurrence of old with new in a file. Fails if old is missing or appears more than once.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["path", "old", "new"]}},
    {"name": "write_file", "description": "Create a file or overwrite it with the given content.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "run_tests", "description": "Run pytest in the repository. Optional args such as 'tests/test_http.py -k configure'. Returns the end of the output.", "input_schema": {"type": "object", "properties": {"args": {"type": "string"}}, "required": []}},
]
KRYPTON_TOOL = {"name": "run_krypton", "description": "Score how structurally heavy the code is: lists flagged files, the heaviest functions and refactoring hints. Pass the files or folders to check, default 'src'.", "input_schema": {"type": "object", "properties": {"paths": {"type": "string"}}, "required": []}}


class Workspace:
    """One arm's working copy of garth plus the tools an agent may use on it. Nothing outside the folder is reachable."""

    def __init__(self, root: Path, python: Path, level: str = "standard") -> None:
        self.root, self.python, self.level = root.resolve(), python, level

    def resolve(self, relative: str) -> Path:
        """The path inside the workspace, or ValueError."""
        target = (self.root / (relative or ".")).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("path is outside the repository")
        return target

    def env(self, pythonpath: str) -> dict[str, str]:
        """A minimal environment for subprocesses: no API keys, nothing from the user's shell but the basics."""
        keep = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "PATHEXT")}
        home = self.root.parent / f"{self.root.name}_home"  # an empty home folder of its own, so tests never touch the user's
        home.mkdir(exist_ok=True)
        return {**keep, "HOME": str(home), "USERPROFILE": str(home), "PYTHONPATH": pythonpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}

    def run(self, command: list[str], pythonpath: str, timeout: int = 180, limit: int = 6000) -> str:
        """Run a command in the workspace; the tail of its output, cut at a line boundary."""
        try:
            done = subprocess.run(command, cwd=self.root, env=self.env(pythonpath), capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return f"timed out after {timeout} seconds"
        text = (done.stdout + done.stderr).decode("utf-8", errors="replace")
        return text if len(text) <= limit else "...\n" + text[-limit:].split("\n", 1)[-1]

    def list_files(self, path: str = ".") -> str:
        folder = self.resolve(path)
        if not folder.is_dir():
            return "not a folder"
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in folder.iterdir() if p.name not in IGNORED)
        return "\n".join(names) or "(empty)"

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        file = self.resolve(path)
        if not file.is_file():
            return "no such file"
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        first, last = max(1, start_line or 1), min(len(lines), end_line or len(lines))
        if last - first > 800:
            last = first + 800
        return "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(first, last + 1)) or "(empty)"

    def grep(self, pattern: str, path: str = ".") -> str:
        base, found = self.resolve(path), []
        files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file() and not any(part in IGNORED for part in p.parts) and p.suffix in (".py", ".md", ".toml", ".txt", ".json", ".yml", ".yaml"))
        rx = re.compile(pattern)
        for file in files:
            for number, line in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    found.append(f"{file.relative_to(self.root).as_posix()}:{number}: {line.strip()[:160]}")
        return "\n".join(found[:60]) + (f"\n... {len(found) - 60} more" if len(found) > 60 else "") if found else "no matches"

    def edit_file(self, path: str, old: str, new: str) -> str:
        file = self.resolve(path)
        text = file.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return f"edit failed: `old` appears {text.count(old)} times; it must appear exactly once"
        file.write_text(text.replace(old, new, 1), encoding="utf-8", newline="")
        return "edited"

    def write_file(self, path: str, content: str) -> str:
        file = self.resolve(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8", newline="")
        return f"wrote {len(content.splitlines())} lines"

    def run_tests(self, args: str = "") -> str:
        return self.run([str(self.python), "-m", "pytest", "-q", "-p", "no:cacheprovider", *shlex.split(args or "")], "src", 180)

    def run_krypton(self, paths: str = "src") -> str:
        targets = [str(self.resolve(p).relative_to(self.root)) for p in shlex.split(paths or "src")]
        return self.run([sys.executable, "-m", "krypton.cli", *targets, "--top", "10", "--level", self.level], str(KRYPTON_SRC), 120, limit=11000)

    def call(self, name: str, arguments: dict) -> str:
        """Run one tool call; errors become the tool's reply so the agent can react."""
        try:
            return getattr(self, name)(**arguments)
        except (ValueError, OSError, re.error, TypeError, AttributeError) as error:
            return f"error: {error}"


@dataclass
class AgentRun:
    """One ticket worked by one agent: every request's raw usage and what the tools did."""

    label: str
    requests: list[dict] = field(default_factory=list)
    tool_calls: dict[str, int] = field(default_factory=dict)
    finished: bool = False
    stop: str = ""
    summary: str = ""


def usage_total(run: AgentRun) -> dict:
    """Summed usage and dollars for a run."""
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    total = {k: sum(r["usage"][k] for r in run.requests) for k in keys}
    total.update(requests=len(run.requests), cost=sum(r["cost"] for r in run.requests), tool_calls=sum(run.tool_calls.values()))
    prompt = total["input_tokens"] + total["cache_read_input_tokens"] + total["cache_creation_input_tokens"]
    total["cached_share"] = total["cache_read_input_tokens"] / prompt if prompt else 0.0
    return total


def agent_run(client, workspace: Workspace, system: str, ticket: str, tools: list[dict], budget: Budget, label: str, max_turns: int) -> AgentRun:
    """Drive one agent on one ticket until it says it is done, runs out of turns, or the budget would be passed."""
    run, messages, last_prompt = AgentRun(label), [{"role": "user", "content": ticket}], 3000
    for _ in range(max_turns):
        worst = (last_prompt * PRICES["write"] + MAX_TOKENS * PRICES["output"]) / 1e6
        try:
            budget.check(worst)
        except BudgetExceeded as stop:
            run.stop = f"budget: {stop}"
            return run
        reply = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, thinking={"type": "between_tools"}, cache_control={"type": "ephemeral"}, system=system, tools=tools, messages=messages)
        usage = reply.usage.model_dump()
        cost = request_cost(usage)
        budget.spent += cost
        last_prompt = usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"] + usage["output_tokens"]
        run.requests.append({"usage": usage, "cost": cost, "stop_reason": reply.stop_reason})
        messages.append({"role": "assistant", "content": reply.content})
        calls = [b for b in reply.content if b.type == "tool_use"]
        if not calls:
            run.finished, run.stop = reply.stop_reason == "end_turn", reply.stop_reason
            run.summary = "".join(getattr(b, "text", "") for b in reply.content)[-400:]
            return run
        results = []
        for call in calls:
            run.tool_calls[call.name] = run.tool_calls.get(call.name, 0) + 1
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": workspace.call(call.name, dict(call.input))[:12000]})
        messages.append({"role": "user", "content": results})
    run.stop = "turn limit"
    return run


def code_stats(workspace: Workspace) -> dict:
    """Size and Krypton's verdict for the source tree, measured with the engine directly."""
    sys.path.insert(0, str(KRYPTON_SRC))
    from krypton.engine.run import run_local

    sources = {p.relative_to(workspace.root).as_posix(): p.read_bytes() for p in (workspace.root / "src").rglob("*.py")}
    result = run_local(sources)
    nested = sum(1 for f in result.findings for fn in f.functions if fn.nested)
    return {"files": len(sources), "lines": sum(f.line_count for f in result.findings), "tokens_est": round(sum(len(s) for s in sources.values()) / 3.5), "purity": result.purity_score,
            "flagged_files": result.flagged_file_count, "nested_functions": nested}


def check_work(workspace: Workspace, hidden: Path) -> dict:
    """Hidden acceptance tests (one pytest run, a ticket passes when all of its tests do) and garth's own suite after a ticket."""
    target = workspace.root / "_hidden" / "test_hidden.py"
    target.parent.mkdir(exist_ok=True)
    shutil.copy(hidden, target)
    text = workspace.run([str(workspace.python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", str(target), "-rA"], "src", 180, limit=200_000)
    shutil.rmtree(target.parent)
    results: dict[int, list[bool]] = {}
    for line in text.splitlines():
        found = re.match(r"(PASSED|FAILED|ERROR) \S*::test_t(\d+)_", line)
        if found:
            results.setdefault(int(found.group(2)), []).append(found.group(1) == "PASSED")
    out = {f"hidden_t{ticket}": bool(results.get(ticket)) and all(results[ticket]) for ticket in TICKETS}
    suite = workspace.run([str(workspace.python), "-m", "pytest", "-q", "-p", "no:cacheprovider"], "src", 180)
    out["suite_ok"] = bool(re.search(r"\b\d+ passed", suite)) and "failed" not in suite and "error" not in suite.lower()
    out["suite_tail"] = suite.strip().splitlines()[-1] if suite.strip() else ""
    return out


def fresh_workspace(source: Path, destination: Path, python: Path) -> Workspace:
    """A clean copy of garth for one arm."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*IGNORED))
    return Workspace(destination, python)


def run_stage(client, workspace: Workspace, label: str, ticket: str, system: str, tools: list[dict], turns: int, budget: Budget, hidden: Path, check: bool = True) -> dict:
    """One agent run on one ticket or cleanup: its usage, what the tools did, the code afterwards and (for tickets) the acceptance checks."""
    run = agent_run(client, workspace, system, ticket, tools, budget, label, turns)
    stage = {"summary": run.summary, "finished": run.finished, "stop": run.stop, "totals": usage_total(run), "tool_calls": run.tool_calls, "requests": run.requests, "after": code_stats(workspace)}
    if check:
        stage["check"] = check_work(workspace, hidden)
    return stage


def run_arm(arm: str, rep: int, client, args, budget: Budget, results: dict) -> None:
    """One arm in one repetition: optional up-front refactor, then the tickets in order."""
    workspace = fresh_workspace(args.source, args.workspaces / f"arm_{arm}{rep}", args.python)
    record = results.setdefault(f"{arm}{rep}", {"arm": arm, "rep": rep, "stages": [], "start": code_stats(workspace)})
    plain, with_krypton = BASE_PROMPT, BASE_PROMPT + (KRYPTON_NEUTRAL if arm == "K" else KRYPTON_RULES)
    stages = ([("refactor", REFACTOR_TICKET, BASE_PROMPT + "\nKrypton is available through the run_krypton tool.", TOOLS + [KRYPTON_TOOL], 70)] if arm == "B" else [])
    for number in args.tasks:
        stages.append((f"ticket {number}", TICKETS[number], with_krypton if arm in "CK" else plain, TOOLS + ([KRYPTON_TOOL] if arm in "CK" else []), 45))
    for label, ticket, system, tools, turns in stages:
        stage = run_stage(client, workspace, f"{arm}/{label}", ticket, system, tools, turns, budget, args.hidden, check=label != "refactor")
        stage["label"] = label
        record["stages"].append(stage)
        save(args, results, budget)
        t = stage["totals"]
        print(f"  {arm}{rep} {label:<9} {t['requests']:>3} requests  ${t['cost']:.3f}  cached {t['cached_share']:.0%}  stop={stage['stop']}  total ${budget.spent:.2f}", flush=True)
        if stage["stop"].startswith("budget"):
            raise BudgetExceeded(stage["stop"])


def save(args, results, budget) -> None:
    """Write everything so far (no key, no prompts, no file contents)."""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"date": date.today().isoformat(), "model": MODEL, "prices_per_mtok": PRICES, "spent_usd": round(budget.spent, 6), "max_usd": budget.cap, "tasks": args.tasks, "tickets": TICKETS}
    args.out.write_text(json.dumps({"meta": meta, "arms": results}, indent=1), encoding="utf-8")


def main(args: argparse.Namespace) -> int:
    """Run the chosen arms; print one line per stage."""
    print(f"arms {args.arms}, tickets {args.tasks}, model {MODEL}, budget ${args.max_usd:.2f}")
    if args.dry:
        return 0
    key = find_key(args.env_root)
    if not key:
        print("no Anthropic key found", file=sys.stderr)
        return 2
    if not args.yes:
        print("re-run with --yes to spend real money", file=sys.stderr)
        return 2
    import anthropic

    client, budget, results = anthropic.Anthropic(api_key=key, max_retries=6), Budget(args.max_usd), {}
    try:
        for rep in range(args.rep_start, args.rep_start + args.reps):
            for arm in (args.arms if rep % 2 == 0 else args.arms[::-1]):  # alternate who goes first
                run_arm(arm, rep, client, args, budget, results)
    except BudgetExceeded as stop:
        print(f"stopped by the budget guard: {stop}")
    print(f"\ntotal spend ${budget.spent:.4f} of ${args.max_usd:.2f}; saved to {args.out}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measure agent cost on original, refactored and Krypton-assisted code.")
    parser.add_argument("--arms", default="ABC")
    parser.add_argument("--tasks", default="123", help="ticket numbers to run, in order")
    parser.add_argument("--max-usd", type=float, default=17.5)
    parser.add_argument("--reps", type=int, default=1, help="repetitions of every arm (interleaved, so a stop leaves the arms balanced)")
    parser.add_argument("--rep-start", type=int, default=0, help="first repetition number, so a second batch can add to the first")
    parser.add_argument("--source", type=Path, required=True, help="a clean copy of garth (with tests)")
    parser.add_argument("--hidden", type=Path, required=True, help="the hidden acceptance test file")
    parser.add_argument("--python", type=Path, required=True, help="a Python with garth's test dependencies installed")
    parser.add_argument("--workspaces", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "cache_anthropic_results" / f"agent_cost_{date.today().isoformat()}.json")
    parser.add_argument("--env-root", type=Path, default=ROOT)
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--yes", action="store_true")
    options = parser.parse_args()
    options.tasks = [int(c) for c in options.tasks]
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(options))
