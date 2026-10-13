"""Inputs for the four stress tests: hand-written samples and generators with known answers."""

from __future__ import annotations

import re

# ---- Test 1: horizontal packing ----------------------------------------------------------

BILLING_JS = """\
function bill(customers, taxTable, discounts, audit) {
  let total = 0;
  let flagged = 0;
  for (const customer of customers) {
    if (!customer.active) continue;
    for (const invoice of customer.invoices) {
      if (invoice.void || invoice.draft) continue;
      for (const line of invoice.lines) {
        if (line.qty <= 0) continue;
        for (const tier of discounts) {
          if (line.qty >= tier.min && line.sku === tier.sku) {
            line.price = line.price * (1 - tier.rate);
          } else if (tier.fallback) {
            line.price = line.price * (1 - tier.fallback);
          }
        }
        const tax = taxTable[line.region] ? taxTable[line.region] : taxTable.default;
        total += line.qty * line.price * (1 + tax);
        if (line.price < 0) flagged++;
      }
      audit.push(invoice.id);
    }
  }
  return flagged > 0 ? -total : total;
}
"""

BILLING_PY = """\
def bill(customers, tax, discounts, audit):
    total = 0
    flagged = 0
    for customer in customers:
        if not customer.active:
            continue
        for invoice in customer.invoices:
            if invoice.void or invoice.draft:
                continue
            for line in invoice.lines:
                if line.qty <= 0:
                    continue
                for tier in discounts:
                    if line.qty >= tier.min and line.sku == tier.sku:
                        line.price = line.price * (1 - tier.rate)
                    elif tier.fallback:
                        line.price = line.price * (1 - tier.fallback)
                rate = tax[line.region] if line.region in tax else tax["default"]
                total += line.qty * line.price * (1 + rate)
                if line.price < 0:
                    flagged += 1
            audit.append(invoice.id)
    return -total if flagged > 0 else total
"""

BILLING_PY_PACKED = """\
def bill(customers, tax, discounts, audit):
    total = 0; flagged = 0
    for customer in customers:
        if not customer.active: continue
        for invoice in customer.invoices:
            if invoice.void or invoice.draft: continue
            for line in invoice.lines:
                if line.qty <= 0: continue
                for tier in discounts:
                    if line.qty >= tier.min and line.sku == tier.sku: line.price = line.price * (1 - tier.rate)
                    elif tier.fallback: line.price = line.price * (1 - tier.fallback)
                rate = tax[line.region] if line.region in tax else tax["default"]; total += line.qty * line.price * (1 + rate)
                if line.price < 0: flagged += 1
            audit.append(invoice.id)
    return -total if flagged > 0 else total
"""


def billing_js(loops: int) -> str:
    """A billing function with `loops` nested for-loops, each with a guard, plus a tax ternary."""
    names = ["customer", "invoice", "line", "tier"][:loops]
    sources = ["customers", "customer.invoices", "invoice.lines", "discounts"][:loops]
    lines = ["function bill(customers, taxTable, discounts, audit) {", "  let total = 0;"]
    for depth, (name, source) in enumerate(zip(names, sources)):
        pad = "  " * (depth + 1)
        lines += [f"{pad}for (const {name} of {source}) {{", f"{pad}  if (!{name}.active) continue;"]
    pad, last = "  " * (loops + 1), names[-1]
    lines += [
        f"{pad}const tax = taxTable[{last}.region] ? taxTable[{last}.region] : taxTable.default;",
        f"{pad}total += {last}.qty * {last}.price * (1 + tax);",
    ]
    lines += ["  " * depth + "}" for depth in range(loops, 0, -1)]
    return "\n".join([*lines, "  return total;", "}"]) + "\n"


def one_line(source: str) -> str:
    """Every newline removed; the code is joined by its own braces and semicolons."""
    return re.sub(r"\s*\n\s*", " ", source).strip() + "\n"


def minified(source: str) -> str:
    """One line with every optional space removed: a crude minifier."""
    squeezed = re.sub(r"\s*([{}();,=<>+\-*/?:!&|\[\].])\s*", r"\1", one_line(source))
    return re.sub(r"\b(const|let|return|of|else|continue)(?=[A-Za-z_(!\-])", r"\1 ", squeezed)


# ---- Test 2: file shattering -------------------------------------------------------------


def shattered_files(count: int) -> dict[str, str]:
    """`count` small modules, each importing state from all the others via ../../ paths."""
    names = [f"module{index}" for index in range(count)]
    files = {}
    for name in names:
        imports = "".join(
            f'import {{ {other}State }} from "../../{other}/lib/{other}State.js";\n' for other in names if other != name
        )
        usage = " + ".join(f"{other}State.value" for other in names if other != name)
        body = f"export const {name}State = {{ value: 1 }};\nexport function read() {{\n  return {usage};\n}}\n"
        files[f"src/{name}/lib/{name}State.js"] = imports + body
    return files


# ---- Test 3: switch-case game ------------------------------------------------------------



def nest_positions(cases: int) -> tuple[int, int, int]:
    """Three cases, spread through the switch, that hide deep control flow."""
    return (cases // 6, cases // 2, cases * 5 // 6)


def _nested_returns(tag: str) -> str:
    """4 nested ifs, both sides returning distinct values: score 1+2+3+4 = 10, 5 outputs."""
    return (
        f'if (p) {{ if (q) {{ if (r) {{ if (s) {{ return "{tag}a"; }} else {{ return "{tag}b"; }} }}'
        f' else {{ return "{tag}c"; }} }} else {{ return "{tag}d"; }} }} else {{ return "{tag}e"; }}'
    )


def _flat_twin(tag: str) -> str:
    """The same four decisions, but sequential and un-nested; one return."""
    return f'if (p) x = "{tag}a"; if (q) x = "{tag}b"; if (r) x = "{tag}c"; if (s) x = "{tag}d"; return x;'


def switch_source(style: str, count: int = 40) -> str:
    """`count` cases. style: flat | nested | flat_twin (same decisions as nested, no nesting)."""
    hidden = nest_positions(count)
    cases = []
    for number in range(count):
        if number in hidden and style == "nested":
            body = _nested_returns(f"c{number}")
        elif number in hidden and style == "flat_twin":
            body = _flat_twin(f"c{number}")
        else:
            body = f'return "v{number}";'
        cases.append(f"    case {number}:\n      {body}")
    prelude = "  let x = null;\n"
    return (
        f"function classify(code, p, q, r, s) {{\n{prelude}  switch (code) {{\n" + "\n".join(cases)
        + '\n    default:\n      return "other";\n  }\n}\n'
    )


def nested_only_source() -> str:
    """The three nested blocks on their own: no switch around them."""
    blocks = "\n  ".join(_nested_returns(f"n{tag}").replace("return", "x =") for tag in nest_positions(40))
    return f"function nestedOnly(p, q, r, s) {{\n  let x = null;\n  {blocks}\n  return x;\n}}\n"


# ---- Test 4: scope isolation tax ----------------------------------------------------------

SCOPE_CHAIN_JS = """\
function outer(a, b, c, d) {
  if (a) {
    class Inner {
      method() {
        if (b) {
          const arrow = () => {
            if (c) {
              const lambda = () => {
                if (d) {
                  return 1;
                }
                return 0;
              };
              return lambda();
            }
            return 2;
          };
          return arrow();
        }
        return 3;
      }
    }
    return new Inner().method();
  }
  return 4;
}
"""

SCOPE_CHAIN_PY = """\
def outer(a, b, c):
    if a:
        def inner(x):
            if b:
                f = lambda y: (lambda z: z if c else 0)(y) if y else None
                return f(x)
            return None
        return inner(1)
    return None
"""


def _ifs(depth: int, inner: str) -> str:
    """`inner` wrapped in `depth` nested ifs."""
    return "if (p) { " * depth + inner + " }" * depth


def chain_js(levels: int, depth: int) -> str:
    """Each function holds `depth` nested ifs, with the next function defined inside them."""
    return f"function f0(p) {{ {_chain_body(levels, depth)} }}\n"


def _chain_body(levels: int, depth: int) -> str:
    """The body of one chain level: `depth` ifs around the next level's arrow function."""
    if levels == 1:
        return _ifs(depth, "")
    return _ifs(depth, f"const next = (p) => {{ {_chain_body(levels - 1, depth)} }};")


def chain_py(levels: int, depth: int) -> str:
    """The Python twin of chain_js: nested defs, each inside `depth` nested ifs."""
    return "def f0(p):\n" + _chain_py_body(levels, depth, indent=1)


def _chain_py_body(levels: int, depth: int, indent: int) -> str:
    lines, pad = [], "    "
    for step in range(depth):
        lines.append(f"{pad * (indent + step)}if p:")
    inner = indent + depth
    if levels == 1:
        lines.append(f"{pad * inner}pass")
    else:
        lines.append(f"{pad * inner}def g(p):")
        lines.append(_chain_py_body(levels - 1, depth, inner + 1).rstrip("\n"))
    return "\n".join(lines) + "\n"


def chain_js_formatted(levels: int, depth: int) -> str:
    """The same chain as chain_js, one statement per line, so each function owns its own lines."""
    return "\n".join(["function f0(p) {", *_formatted_body(levels, depth, 1), "}"]) + "\n"


def _formatted_body(levels: int, depth: int, indent: int) -> list[str]:
    pad = "  "
    lines = [f"{pad * (indent + step)}if (p) {{" for step in range(depth)]
    inner = indent + depth
    if levels == 1:
        lines.append(f"{pad * inner}return p;")
    else:
        lines.append(f"{pad * inner}const next = (p) => {{")
        lines += _formatted_body(levels - 1, depth, inner + 1)
        lines.append(f"{pad * inner}}};")
    return lines + [f"{pad * (indent + step)}}}" for step in reversed(range(depth))]


def expected_chain_score(levels: int, depth: int, cap: int = 1_000_000) -> int:
    """S(last) = own; S(i) = own + (1 + depth) * S(i + 1); own = 1 + 2 + ... + depth."""
    own = depth * (depth + 1) // 2
    score = own
    for _ in range(levels - 1):
        score = min(cap, own + (1 + depth) * score)
    return min(cap, score)


def callback_nest(levels: int, wrap: int) -> str:
    """Node-style callbacks: each level has an error guard, then defines the next callback
    inside `wrap` extra if-blocks (0 = defined directly in the body, so the tax is x1)."""

    def body(remaining: int) -> str:
        guard = "if (err) return done(err); "
        if remaining == 1:
            return guard + "return done(null);"
        return guard + _ifs(wrap, f"load((err, data) => {{ {body(remaining - 1)} }});")

    return f"function route(done) {{ load((err, data) => {{ {body(levels)} }}); }}\n"


def deep_arrows(levels: int) -> str:
    """`levels` arrow functions nested directly in each other, a ternary at the bottom."""
    return "f = " + "() => " * levels + "(a ? 1 : 2);\n"
