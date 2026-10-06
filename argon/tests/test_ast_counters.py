from pathlib import Path

from argon.engine.ast_counters import (
    analyze_tree,
    count_async_boundaries,
    count_cyclomatic_branches,
    count_determinate_outputs,
    count_relative_imports,
    count_weighted_branches,
    find_branch_nodes,
    parse_source,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_branches():
    tree = parse_source("branches.js", (FIXTURES / "branches.js").read_bytes())

    assert count_cyclomatic_branches(tree) == 5


def test_chaos_inner_function_tax():
    # if a: +1 at depth 0; inner() is defined at depth 1 and scores 1 on its own
    # scale, so it passes up 1 * (1 + 1) = 2. Flat cyclomatic count stays 2.
    for name in ("chaos.py", "chaos.js"):
        tree = parse_source(name, (FIXTURES / name).read_bytes())

        assert count_cyclomatic_branches(tree) == 2, name
        assert count_weighted_branches(tree) == 3, name
        assert [branch.depth for branch in find_branch_nodes(tree)] == [0, 0], name


def test_per_function_scores():
    tree = parse_source("chaos.py", (FIXTURES / "chaos.py").read_bytes())

    functions = {function.name: function for function in analyze_tree(tree).functions}

    # outer: if a (1) + inner's tax 1 * (1 + 1); inner scores 1 on its own scale.
    outer, inner = functions["outer"], functions["outer.inner"]
    assert (outer.score, outer.branches, outer.start_line, outer.line_ranges) == (3, 1, 1, ((1, 2),))
    # inner is defined inside `if a`, so its effective depth carries that level (A6).
    assert (inner.score, inner.max_depth, inner.line_ranges) == (1, 2, ((3, 5),))


def test_only_named_functions_get_rows():
    # §8.1: declarations, methods, and functions that are the direct value of a binding.
    source = b"""\
const a = () => 1;
obj.b = function () {};
x = {c: () => 1};
items.map((i) => i);
const findRoutes = useCallback(async () => {}, []);
app.post("/api", (req, res) => {});
class Cart { total() {} d = () => 1; }
"""
    tree = parse_source("names.js", source)

    assert [function.name for function in analyze_tree(tree).functions] == ["a", "c", "Cart.total", "Cart.d"]


def test_anonymous_children_merge_into_their_row():
    source = b"function load(items) {\n  items.forEach((item) => { if (item) {} });\n}\n"
    tree = parse_source("merge.js", source)

    (load,) = analyze_tree(tree).functions
    assert (load.name, load.branches, load.score, load.max_depth) == ("load", 1, 1, 1)


def test_python_row_names():
    source = b"""\
handler = register(lambda event: event)
sorted(rows, key=lambda row: row[0])
f = lambda value: value
self.x = lambda: 1
"""
    tree = parse_source("names.py", source)

    assert [function.name for function in analyze_tree(tree).functions] == ["f"]


def test_nested_if_else_compounds():
    source = b'''\
if a:
    if b:
        if c:
            pass
        else:
            pass
    else:
        pass
else:
    pass
'''
    tree = parse_source("nested.py", source)

    assert count_weighted_branches(tree) == 6  # 1 + 2 + 3


def test_else_if_chains_stay_flat():
    assert count_weighted_branches(parse_source("chain.js", b"if (a) {} else if (b) {} else if (c) {}")) == 3
    elif_chain = b"if a:\n    pass\nelif b:\n    pass\nelif c:\n    pass\n"
    assert count_weighted_branches(parse_source("chain.py", elif_chain)) == 3
    assert count_weighted_branches(parse_source("ternary.js", b"x = a ? b : c ? d : e;")) == 2


def test_comprehension_scores_like_its_unrolled_loop():
    # [f(x) for a in b if c for x in a]  ==  for a in b: / if c: / for x in a: f(x)
    comprehension = b"rows = [f(x) for a in b if c for x in a]\n"
    unrolled = b"for a in b:\n    if c:\n        for x in a:\n            rows.append(f(x))\n"

    expected = 1 + 2 + 3
    assert count_weighted_branches(parse_source("comp.py", comprehension)) == expected
    assert count_weighted_branches(parse_source("loop.py", unrolled)) == expected


def test_comprehension_element_is_innermost():
    # The ternary in the element runs inside the loop: depth 1, costs 2.
    source = b"labels = [('big' if x > 9 else 'small') for x in xs]\n"
    tree = parse_source("element.py", source)

    assert count_weighted_branches(tree) == 1 + 2


def test_comprehension_in_loop_header_is_not_nested():
    # `for r in (p for p in ps if p)` unrolls to a separate loop before the for.
    source = b"for root in (path for path in paths if path.is_dir()):\n    pass\n"
    tree = parse_source("header.py", source)

    assert count_weighted_branches(tree) == 1 + (1 + 2)


def test_boolean_operators_stay_flat():
    source = b"for x in y:\n    if a and b:\n        pass\n"
    tree = parse_source("bool.py", source)

    assert count_weighted_branches(tree) == 1 + 2 + 1


def test_nested_scope_tax_compounds():
    source = b'''\
def outer():
    for x in xs:
        if x:
            def inner():
                if y:
                    if z:
                        pass
'''
    tree = parse_source("closure.py", source)

    # outer: for 1 + if 2; inner (1 + 2 = 3) defined at depth 2 pays 3 * 3 = 9.
    assert count_weighted_branches(tree) == 12


def test_deep_expression_does_not_hit_recursion_limit():
    source = ("x = " + " + ".join(["a"] * 20_000) + "\nif x:\n    pass\n").encode()

    assert count_weighted_branches(parse_source("deep.py", source)) == 1


def test_deeply_nested_scopes_have_no_recursion_limit():
    # Each arrow is defined at depth 0 of its parent, so every tax is * 1.
    source = ("f = " + "() => " * 5_000 + "(a ? 1 : 2);\n").encode()

    assert count_weighted_branches(parse_source("scopes.js", source)) == 1


def test_outputs():
    tree = parse_source("branches.js", (FIXTURES / "branches.js").read_bytes())

    assert count_determinate_outputs(tree) == 3


def test_async_dedupe():
    source = b'''\
socket.on("message", a);
socket.on("close", b);
queue.on("job", c);
bus.subscribe(d);
'''
    tree = parse_source("async.js", source)

    assert count_async_boundaries(tree) == 3


def test_async_new():
    tree = parse_source("async.js", b'new WebSocket(url); new Worker("worker.js");')

    assert count_async_boundaries(tree) == 2


def test_onmessage():
    source = b"socket.onmessage = a; socket.onmessage = b; worker.onmessage = c;"
    tree = parse_source("async.js", source)

    assert count_async_boundaries(tree) == 2


def test_import_ext():
    tree = parse_source("src/a.ts", b'import "./b";')

    assert count_relative_imports(tree, "src/a.ts", {"src/b.ts"}) == 1


def test_import_dedupe():
    tree = parse_source("src/a.ts", b'import "./b"; import "./b.js";')

    assert count_relative_imports(tree, "src/a.ts", {"src/b.ts"}) == 1


def test_package():
    tree = parse_source("src/a.ts", b'import "react";')

    assert count_relative_imports(tree, "src/a.ts", {"src/react.ts"}) == 0


def test_dynamic_template():
    tree = parse_source("src/a.ts", b'import(`./${moduleName}`);')

    assert count_relative_imports(tree, "src/a.ts", {"src/moduleName.ts"}) == 0


def test_empty_require():
    tree = parse_source("src/a.ts", b"require();")

    assert count_relative_imports(tree, "src/a.ts", {"src/moduleName.ts"}) == 0


def test_all_metrics():
    tree = parse_source("branches.js", (FIXTURES / "branches.js").read_bytes())

    metrics = analyze_tree(tree)

    assert len(metrics.branches) == 5
    assert [function.outputs_effective for function in metrics.functions] == [3]


def test_tsx():
    tree = parse_source("component.tsx", b"const view = <div />;")

    assert not tree.has_error


def test_python_branches():
    source = b'''\
def grade(value):
    if value > 0 and value < 10:
        return 1
    elif value < 0:
        return 0
    for item in range(value):
        pass
    while value:
        value -= 1
    try:
        pass
    except ValueError:
        pass
    return 1 if value else 0
'''
    tree = parse_source("grade.py", source)

    assert not tree.has_error
    assert count_cyclomatic_branches(tree) == 7


def test_python_match_cases():
    source = b'''\
def choose(value):
    match value:
        case 1:
            return "one"
        case 2 | 3:
            return "small"
        case name:
            return name
'''
    tree = parse_source("match.py", source)

    assert not tree.has_error
    assert count_cyclomatic_branches(tree) == 2


def test_python_comprehension_branches():
    source = b"def select(values):\n    return [x for x in values if x and x > 0 if x < 10]\n"
    tree = parse_source("comprehension.py", source)

    assert count_cyclomatic_branches(tree) == 4


def test_python_assert_and_with():
    source = b"def check(stream, value):\n    with stream:\n        assert value\n"
    tree = parse_source("assert_with.py", source)

    assert count_cyclomatic_branches(tree) == 1  # radon: assert +1, with +0


def test_js_with_is_not_a_branch():
    tree = parse_source("legacy.js", b"with (scope) { run(); }")

    assert count_cyclomatic_branches(tree) == 0


def test_python_loop_and_try_else():
    source = b'''\
for item in items:
    pass
else:
    pass
while ready:
    pass
else:
    pass
try:
    pass
except ValueError:
    pass
else:
    pass
if ready:
    pass
else:
    pass
'''
    tree = parse_source("else.py", source)

    assert count_cyclomatic_branches(tree) == 7  # radon: for 2, while 2, try 2, if 1


def test_python_comprehension_per_for_clause():
    source = b"pairs = [(a, b) for a in left if a for b in right]\n"
    tree = parse_source("pairs.py", source)

    assert count_cyclomatic_branches(tree) == 3  # radon: two for clauses + one if


def test_python_match_guards_and_irrefutable_patterns():
    source = b'''\
match value:
    case 1 if ready:
        pass
    case (2):
        pass
    case x,:
        pass
    case other if other > 3:
        pass
    case (_):
        pass
'''
    tree = parse_source("match_edges.py", source)

    # radon: five cases minus one for having a catch-all; guards are not counted.
    assert count_cyclomatic_branches(tree) == 4


def test_python_except_star():
    source = b"try:\n    pass\nexcept* ValueError:\n    pass\nexcept* TypeError:\n    pass\n"
    tree = parse_source("groups.py", source)

    assert count_cyclomatic_branches(tree) == 2


def test_python_async_boundaries():
    # §9.2: listeners deduplicated by receiver; decorators and constructors per site.
    source = b'''\
bus.subscribe("a", on_a)
bus.subscribe("b", on_b)
channel.basic_consume(queue, handler)
consumer = KafkaConsumer("orders")

@app.task
def work():
    pass

@shared_task
def other():
    pass

async def read_all(source):
    await source.open()
    async for item in source:
        pass
'''
    tree = parse_source("async_source.py", source)

    assert not tree.has_error
    assert count_async_boundaries(tree) == 2 + 1 + 2  # bus, channel; KafkaConsumer; two decorators


def test_python_relative_imports():
    source = b'''\
from . import sibling
from ..utils import parse
from ..utils import parse as parse_again
from typing import Any
'''
    tree = parse_source("src/pkg/sub/module.py", source)
    pr_files = {
        "src/pkg/sub/module.py",
        "src/pkg/sub/__init__.py",
        "src/pkg/sub/sibling.py",
        "src/pkg/utils.py",
        "src/pkg/__init__.py",
    }

    assert count_relative_imports(tree, "src/pkg/sub/module.py", pr_files) == 3


def test_broken():
    tree = parse_source("broken.js", b"function broken( {")

    assert tree.has_error


def test_empty_file_is_not_an_error():
    assert not parse_source("empty.py", b"").has_error
    assert not parse_source("empty.ts", b"").has_error


def test_python_coding_cookie():
    source = "# -*- coding: latin-1 -*-\nname = 'café' if ready else ''\n".encode("latin-1")
    tree = parse_source("legacy.py", source)

    assert not tree.has_error
    assert count_cyclomatic_branches(tree) == 1


def test_invalid_utf8_does_not_crash():
    tree = parse_source("bad.js", b"const s = '\xff';\nif (s) {}\n")

    assert count_cyclomatic_branches(tree) == 1


def test_mts_cts():
    assert parse_source("a.mts", b"export const x: number = 1;").language == "typescript"
    assert parse_source("a.cts", b"const x: number = 1;").language == "typescript"


def test_functions_sharing_a_line_split_it():
    # Test 1: attribution was per physical line, so siblings on one line each took all of it
    # and the enclosing function got none.
    source = b"function outer(){ function a(){ if(x){y()} } function b(){ if(x){y()} } return 1 }\n"
    metrics = analyze_tree(parse_source("shared.js", source))
    own = {f.name: f.effective_lines(metrics.line_weights) for f in metrics.functions}

    assert own["outer"] > 0
    assert own["outer.a"] == own["outer.b"]
    assert sum(own.values()) <= metrics.line_weights.total + len(own)  # rounding only
    assert max(own.values()) < 2 * metrics.line_weights.total
