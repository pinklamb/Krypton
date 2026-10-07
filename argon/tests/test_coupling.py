from argon.engine.config import EngineConfig
from argon.engine.coupling import coupling_weights, namespace_edges
from argon.engine.run import run_local

NL = chr(10)


def _hub_imports(count: int, lines: int) -> bytes:
    imports = [f"import {{ v{i} }} from './m{i}.js';" for i in range(count)]
    body = [f"const x{i} = v{i % count} + {i};" for i in range(lines)]
    return NL.join(imports + body).encode() + b"\n"


def _modules(count: int) -> dict[str, bytes]:
    return {f"m{i}.js": f"export const v{i} = {i};\n".encode() for i in range(count)}


def test_weights_count_distinct_neighbours_both_ways():
    imports = {("a", "b"), ("a", "c"), ("d", "a")}
    shared = {("a", "b"), ("e", "a")}  # a -> b again (not double counted), e -> a

    assert coupling_weights(["a", "b", "c", "d", "e"], imports, shared) == {"a": 4, "b": 1, "c": 1, "d": 1, "e": 1}


def test_namespace_needs_two_defining_files_and_ignores_self_edges():
    defs = {"world.js": {("Racer", "world")}, "player.js": {("Racer", "player")}, "solo.js": {("Lonely", "x")}}
    refs = {"world.js": {("Racer", "player"), ("Racer", "world")}, "main.js": {("Racer", "world"), ("Lonely", "x")}}

    assert namespace_edges(defs, refs, min_files=2) == {("world.js", "player.js"), ("main.js", "world.js")}


def test_import_hub_over_threshold_is_flagged_hotspot():
    files = {**_modules(13), "hub.js": _hub_imports(13, 50)}

    findings = {f.filename: f for f in run_local(files).findings}

    assert findings["hub.js"].coupling_weight == 13
    assert (findings["hub.js"].flagged, findings["hub.js"].flag_reason) == (True, "coupling_hotspot")
    assert not findings["m0.js"].flagged  # each leaf is only coupled to the hub


def test_small_barrel_file_is_not_a_hotspot():
    files = {**_modules(13), "index.js": _hub_imports(13, 3)}

    finding = next(f for f in run_local(files).findings if f.filename == "index.js")

    assert finding.coupling_weight == 13 and not finding.flagged


def test_shared_global_namespace_counts_without_any_import():
    providers = {f"mod{i}.js": f"Racer.mod{i} = {{ run() {{ return {i}; }} }};{NL}".encode() for i in range(13)}
    hub_body = NL.join(f"Racer.mod{i}.run();" for i in range(13)) + NL + NL.join(f"const t{i} = {i};" for i in range(45)) + NL
    files = {**providers, "world.js": hub_body.encode()}

    findings = {f.filename: f for f in run_local(files).findings}

    assert findings["world.js"].relative_import_count == 0
    assert findings["world.js"].coupling_weight == 13
    assert findings["world.js"].flag_reason == "coupling_hotspot"


def test_threshold_is_configurable():
    files = {**_modules(13), "hub.js": _hub_imports(13, 50)}

    lenient = run_local(files, cfg=EngineConfig(coupling_weight_threshold=20))

    assert all(f.flag_reason != "coupling_hotspot" for f in lenient.findings)
