"""The audit page builds, covers every module, and its spec references stay valid."""

from pathlib import Path

from tools.build_docs import build, check_spec_map
from tools.collect import analyze_module, collect_tests, product_files
from tools.docs_html import slug

ROOT = Path(__file__).resolve().parent.parent


def test_audit_page_builds_and_lists_every_function(tmp_path):
    out = build(tmp_path / "audit.html", run_tests=False)
    page = out.read_text(encoding="utf-8")

    for path in product_files(ROOT):
        module = analyze_module(ROOT, path)
        assert f'id="{slug(path.relative_to(ROOT).as_posix())}"' in page, path
        assert all(entry.qualname in page for entry in module.entries)
    assert "Krypton" in page and "<script>" in page


def test_spec_references_are_all_valid():
    modules = [analyze_module(ROOT, path) for path in product_files(ROOT)]

    assert check_spec_map(modules, collect_tests(ROOT)) == []


def test_every_product_definition_has_a_docstring():
    undocumented = [
        f"{module.path}:{entry.start} {entry.qualname}"
        for module in (analyze_module(ROOT, path) for path in product_files(ROOT))
        for entry in module.entries
        if not entry.doc
    ]

    assert undocumented == []
