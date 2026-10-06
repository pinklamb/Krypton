"""Test 1 addition: do functions that share a physical line get the right share of its lines?"""

from __future__ import annotations

import re

from argon.engine.ast_counters import analyze_tree
from argon.engine.config import DEFAULT_CONFIG as CFG
from argon.engine.languages import parse_source
from tools.stress_checks import FINDING, PASS, Check

NESTED = """function outer(p) {
  if (p) {
    if (p) {
      const inner = (q) => {
        if (q) {
          if (q) {
            return 1;
          }
        }
        return 0;
      };
      return inner(p);
    }
  }
  return 2;
}
"""
SIBLINGS = [f"function f{i}(a) {{ if (a) {{ return {i}; }} return 0; }}" for i in range(50)]


def one_line(source: str) -> str:
    return re.sub(r"\s*\n\s*", " ", source).strip() + "\n"


def own_lines(source: str, name: str) -> tuple[dict[str, int], int]:
    """({function: its own effective lines}, the file's effective lines)."""
    metrics = analyze_tree(parse_source(name, source.encode()), CFG, name)
    return {f.name: f.effective_lines(metrics.line_weights) for f in metrics.functions}, metrics.line_weights.total


def attribution() -> tuple[list[list[str]], Check]:
    nested_formatted, nested_total = own_lines(NESTED, "a.js")
    nested_packed, nested_packed_total = own_lines(one_line(NESTED), "a.js")
    sibling_formatted, sibling_total = own_lines("\n".join(SIBLINGS) + "\n", "s.js")
    sibling_packed, _ = own_lines("".join(SIBLINGS) + "\n", "s.js")
    rows = [
        ["outer (named function containing another)", str(nested_formatted["outer"]), str(nested_packed["outer"])],
        ["outer.inner (the nested one)", str(nested_formatted["outer.inner"]), str(nested_packed["outer.inner"])],
        ["file total", str(nested_total), str(nested_packed_total)],
        ["each of 50 sibling functions", f"{max(sibling_formatted.values())}", f"{max(sibling_packed.values())}"],
        ["all 50 siblings added up (file total is " + str(sibling_total) + ")", str(sum(sibling_formatted.values())), str(sum(sibling_packed.values()))],
    ]
    zeroed = nested_packed["outer"] == 0 and nested_formatted["outer"] > 0
    inflated = max(sibling_packed.values()) > 2 * max(sibling_formatted.values())
    verdict = FINDING if zeroed or inflated else PASS
    detail = (
        f"{'No longer' if verdict == PASS else 'Yes'}: lines shared by several functions are split by character ownership. "
        f"Nested: packed onto one line the outer function keeps {nested_packed['outer']} effective lines (formatted: {nested_formatted['outer']}) and the inner one {nested_packed['outer.inner']} (formatted: {nested_formatted['outer.inner']}). "
        f"Siblings: each of 50 functions on one line gets {max(sibling_packed.values())} (formatted: {max(sibling_formatted.values())}); per-function total {sum(sibling_packed.values()):,} against a file total of {sibling_total}. "
        "Before the fix the outer function fell to 0 and each sibling was credited the whole line (so the 50 summed to 50 times the file). The split is by non-space code characters, so it follows what each function actually holds; rounding to whole lines can leave the sum a few lines off the file total."
    )
    return rows, Check("Does packing several functions onto one line break per-function sizes?", verdict, detail)
