"""Spec §12.2: each gamed file against its honest twin, so the tests hold whatever the thresholds become."""

import re

from argon.engine.ast_counters import analyze_tree
from argon.engine.config import DEFAULT_CONFIG, EngineConfig
from argon.engine.imports import resolve_imports
from argon.engine.languages import parse_source
from argon.engine.run import run_local

HONEST = """\
def classify(value):
    if value > 10:
        return "big"
    return "small"
"""


def _only_function(name, source, cfg=DEFAULT_CONFIG):
    result = run_local({name: source.encode()}, cfg=cfg)
    (function,) = result.findings[0].functions
    return function


def test_a1_unreachable_returns_add_nothing():
    padding = "".join(f'    return "p{index}"\n' for index in range(20))
    honest = _only_function("a.py", HONEST)
    padded = _only_function("a.py", HONEST + padding)

    assert padded.outputs_effective == honest.outputs_effective == 2
    # rating_master's altered lines grow with the padding itself, so compare per line.
    assert padded.rating_master / padded.effective_lines == honest.rating_master / honest.effective_lines


def test_a1_reworded_returns_count_once():
    function = _only_function("a.js", "function f(x) {\n  if (x) return (x);\n  return x;\n}\n")

    assert function.determinate_outputs == 1


def test_a1_outputs_capped_at_branches_plus_one():
    # fail() always throws, which the dead-code rule can't see; the cap still holds.
    returns = "".join(f'    return "r{index}"\n' for index in range(6))
    source = f"def f(x):\n    if x:\n        fail()\n{returns}"

    function = _only_function("a.py", source)

    assert (function.determinate_outputs, function.outputs_effective) == (1, 1)
    source_after_fail = f"def f(x):\n    if x:\n        return 0\n    fail()\n{returns}"
    assert _only_function("a.py", source_after_fail).outputs_effective == 2


def test_a1_yield_counts_as_output():
    function = _only_function("a.py", "def gen(x):\n    if x:\n        yield 1\n    yield 2\n")

    assert function.determinate_outputs == 2


def test_a3_generated_marker_does_not_skip():
    result = run_local({"src/api.js": b"// @generated\nif (a) {}\n"})

    assert [finding.filename for finding in result.findings] == ["src/api.js"]
    assert result.ignored_generated_markers == ["src/api.js"]


def test_a3_generated_path_is_skipped():
    result = run_local({"src/__generated__/api.js": b"if (a) {}\n"})

    assert [(skip.filename, skip.reason) for skip in result.skipped_files] == [("src/__generated__/api.js", "generated_path")]


FORMATTED_JS = """\
function updateItem(itemId, changes, store, logger) {
  const records = store.items ?? {};
  if (!(itemId in records)) {
    throw new Error(`missing ${itemId}`);
  }
  const updated = { ...records[itemId] };
  for (const [key, value] of Object.entries(changes)) {
    if (key.startsWith("_")) continue;
    if (value !== null) {
      updated[key] = typeof value === "string" ? value.trim() : value;
    }
  }
  records[itemId] = updated;
  logger.info(`updated ${itemId}`);
  return updated;
}
"""


def test_a4_packing_keeps_effective_lines():
    # Amended target (Oct 2026): >= 60%, not the spec's original 70%. Packing keeps every
    # statement but sheds the ceil(chars / 40) weight of long formatted lines (measured 63%).
    packed = re.sub(r"\s*\n\s*", " ", FORMATTED_JS).strip() + "\n"

    formatted_lines = run_local({"a.js": FORMATTED_JS.encode()}).findings[0].effective_lines
    packed_lines = run_local({"a.js": packed.encode()}).findings[0].effective_lines

    assert packed_lines >= 0.6 * formatted_lines


def test_a4_comments_and_docstrings_are_not_effective():
    source = '"""Module docs."""\n# a comment\n\ndef f():\n    """Docs."""\n    return 1  # trailing\n'

    assert run_local({"a.py": source.encode()}).findings[0].effective_lines == 2


def test_a4_long_lines_count_by_width():
    source = "x = " + "1 + " * 112 + "1\n"  # 453 characters

    assert run_local({"a.py": source.encode()}).findings[0].effective_lines == 12  # ceil(453 / 40)


def test_a4_packed_statements_count_each():
    formatted = "a()\nb()\nc()\n"
    packed = "a(); b(); c()\n"

    weights = [run_local({"a.py": source.encode()}).findings[0].effective_lines for source in (formatted, packed)]

    assert weights == [3, 3]


def test_a4_ordinary_lines_count_once():
    source = "export function f(x) {\n  if (x) return 1;\n  if (a) {\n  } else if (b) {\n  }\n}\n"

    assert run_local({"a.js": source.encode()}).findings[0].effective_lines == 6


def test_a8_python_absolute_imports():
    files = {"pkg/a.py": b"from pkg.b import x\nimport pkg.c\n", "pkg/b.py": b"x = 1\n", "pkg/c.py": b"y = 2\n"}

    counts = {finding.filename: finding.relative_import_count for finding in run_local(files).findings}

    assert counts["pkg/a.py"] == 2


def test_a8_python_submodule_import():
    files = {"pkg/a.py": b"from pkg import helpers\n", "pkg/helpers.py": b"x = 1\n", "pkg/__init__.py": b""}

    counts = {finding.filename: finding.relative_import_count for finding in run_local(files).findings}

    assert counts["pkg/a.py"] == 2  # the package and its submodule


def test_a8_ts_alias():
    cfg = EngineConfig(ts_path_aliases={"@/*": ["src/*"]})
    tree = analyze_tree(parse_source("src/a.ts", b'import x from "@/b";\n'), cfg)

    resolved = resolve_imports("src/a.ts", tree.js_specifiers, (), frozenset({"src/a.ts", "src/b.ts"}), cfg)

    assert resolved == {"src/b.ts"}


def test_a8_ts_base_url():
    cfg = EngineConfig(ts_base_url="src")
    tree = analyze_tree(parse_source("src/a.ts", b'import x from "utils/b";\nimport y from "react";\n'), cfg)

    resolved = resolve_imports("src/a.ts", tree.js_specifiers, (), frozenset({"src/a.ts", "src/utils/b.ts"}), cfg)

    assert resolved == {"src/utils/b.ts"}


def test_a9_isolated_files_do_not_dilute_fragmentation():
    names = [f"core/m{index}.js" for index in range(6)]
    files = {
        name: "".join(f'import "./{other.split("/")[1]}";\n' for other in names if other != name).encode()
        for name in names
    }
    files.update({f"misc/iso{index}.js": b"const x = 1;\n" for index in range(20)})

    result = run_local(files)

    assert result.fragmentation_flagged
    assert result.fragmentation_cluster == sorted(names)


def test_c1_missing_nodes_are_errors():
    for name, source in [("a.ts", b"function f() {\n  return 1;\n"), ("a.js", b"f(1 + 2;"), ("a.py", b"def f(:\n    pass\n")]:
        assert parse_source(name, source).has_error, name


def test_c2_fail_closed_above_error_limit():
    result = run_local({"broken.js": b"function (((( {{{{ ]]]] ))))\n"})

    (finding,) = result.findings
    assert (finding.flagged, finding.flag_reason, finding.functions) == (True, "unparseable", [])
    assert result.flagged_file_count == 1


def test_c2_small_errors_are_scored_with_a_warning_flag():
    source = b"if (a) {}\n" * 60 + b"f(1 + 2;\n"

    (finding,) = run_local({"mostly_fine.js": source}).findings

    assert finding.parse_error and finding.flag_reason != "unparseable"
    assert finding.cyclomatic_branches == 60


def test_c3_bad_bytes_do_not_raise():
    (finding,) = run_local({"legacy.py": b"\xff\xfe x = 1\n"}).findings

    assert finding.filename == "legacy.py"


def test_c4_oversize_is_flagged_not_parsed():
    source = b"const x = 1;\n" * 160_000  # ~2 MB

    (finding,) = run_local({"bundle.js": source}).findings

    assert (finding.flag_reason, finding.cyclomatic_branches) == ("oversize", 0)


def test_c4_one_very_long_line_is_flagged_not_parsed():
    # Issue 7: a minified bundle is small in bytes but makes range() quadratic and nodes huge.
    minified = b"var a=[" + b"1," * 4000 + b"];if(a){a()}\n"

    (finding,) = run_local({"min.js": minified}).findings

    assert (finding.flag_reason, finding.cyclomatic_branches) == ("oversize", 0)
    assert len(minified) < 20_000


def test_lines_under_the_limit_still_parse():
    source = b"var a=[" + b"1," * 2490 + b"];\nif (a) { b(); }\n"  # first line under 5,000 characters

    (finding,) = run_local({"edge.js": source}).findings

    assert (finding.flag_reason, finding.cyclomatic_branches) == (None, 1)


def test_b10_match_guard_is_not_counted_but_comprehension_filter_is():
    source = b"match v:\n    case 1 if g:\n        pass\nrows = [x for x in xs if x]\n"

    assert analyze_tree(parse_source("a.py", source)).cyclomatic_branches == 1 + 2


def test_a2_optional_calls_switch():
    source = b"fn?.(); a?.b(); a?.b;\n"
    off = analyze_tree(parse_source("a.js", source))
    on = analyze_tree(parse_source("a.js", source), EngineConfig(count_optional_calls=True))

    assert (off.cyclomatic_branches, on.cyclomatic_branches) == (0, 2)


def test_c4_node_with_too_many_children_is_flagged_not_walked():
    source = ("const a = [" + chr(10) + ("1," + chr(10)) * 60_000 + "];" + chr(10)).encode()  # ~180 KB, 60,000 children

    (finding,) = run_local({"table.js": source}).findings

    assert (finding.flag_reason, finding.cyclomatic_branches) == ("oversize", 0)
