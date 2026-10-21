"""Bundled libraries are left out of the score, and first-party code is not."""

from krypton.engine.config import DEFAULT_CONFIG, matches_any
from krypton.engine.run import run_local
from krypton.engine.vendored import linguist_globs, looks_vendored

BOOTSTRAP = "/*!\n * Bootstrap v5.3.2 (https://getbootstrap.com/)\n * Copyright 2011-2023 The Bootstrap Authors\n * Licensed under MIT\n */\n" + "".join(f"function f{i}(a) {{ if (a) {{ return {i}; }} return 0; }}\n" for i in range(80))
FIRST_PARTY = "/* Copyright (c) 2024 Acme Corp. v1.2.0. Licensed under the MIT license. */\n" + "".join(f"function g{i}(a) {{ if (a) {{ return {i}; }} return 0; }}\n" for i in range(80))
WEBPACK = "(function(modules){ function __webpack_require__(id){} })([function(){}]);\n" + "var a = 1;\n" * 300
MINIFIED = "!function(e){" + "var a=1,b=2,c=function(x){return x+1};" * 60 + "}(window);\n" * 3


def test_a_named_library_in_an_assets_folder_is_left_out():
    assert looks_vendored("public/js/bootstrap.js", BOOTSTRAP) == "vendored_name"
    assert looks_vendored("static/jquery-3.6.0.js", "var x = 1;\n" * 400) == "vendored_name"      # the version in the name is a second sign


def test_a_bundler_fingerprint_in_an_assets_folder_is_left_out():
    assert looks_vendored("static/js/main.3f2a1.js", WEBPACK) == "vendored_content"
    assert looks_vendored("src/main.js", WEBPACK) is None                                             # a fingerprint alone, outside an assets folder, is not enough


def test_minified_shape_with_a_banner_is_left_out():
    assert looks_vendored("src/vendorish.js", "/*! SomeLib v2.4.1 | MIT License */\n" + MINIFIED) == "vendored_content"


def test_first_party_code_is_never_left_out_for_a_license_banner_or_a_library_like_name():
    assert looks_vendored("src/app.js", FIRST_PARTY) is None
    assert looks_vendored("public/app.js", FIRST_PARTY) is None                                       # banner plus an assets folder: both are weak signs
    assert looks_vendored("src/lib/chart.js", FIRST_PARTY) is None                                    # a library-like name with no second sign
    assert looks_vendored("src/components/vue.js", "export const x = 1;\n" * 50) is None


def test_python_is_decided_by_path_only():
    assert looks_vendored("pkg/_vendor/six.py", BOOTSTRAP) is None
    assert matches_any("pkg/_vendor/six.py", DEFAULT_CONFIG.vendored_path_globs)
    assert matches_any(".venv/lib/python3.12/site-packages/x/y.py", DEFAULT_CONFIG.vendored_path_globs)


def test_the_wider_path_rules_cover_asset_vendor_folders_and_minified_names():
    for path in ("static/js/vendor-libs/a.js", "public/assets/vendor/a.js", "bower_components/x/a.js", "app/wwwroot/lib/bootstrap/a.js", "third_party/a.py"):
        assert matches_any(path, DEFAULT_CONFIG.vendored_path_globs), path
    for path in ("static/app.min.js", "static/app-min.js", "dist2/app.bundle.js", "x/a.chunk.js"):
        assert matches_any(path, DEFAULT_CONFIG.generated_path_globs), path
    for path in ("src/lib/helpers.js", "static/app.js", "src/external/client.ts"):
        assert not matches_any(path, DEFAULT_CONFIG.vendored_path_globs + DEFAULT_CONFIG.generated_path_globs), path


def test_run_local_reports_the_reason_and_scores_only_the_rest():
    files = {"public/js/bootstrap.js": BOOTSTRAP.encode(), "src/app.js": FIRST_PARTY.encode(), "static/js/main.1a2b.js": WEBPACK.encode()}
    result = run_local(files)
    assert [f.filename for f in result.findings] == ["src/app.js"]
    assert {s.filename: s.reason for s in result.skipped_files} == {"public/js/bootstrap.js": "vendored_name", "static/js/main.1a2b.js": "vendored_content"}
    off = run_local(files, cfg=DEFAULT_CONFIG.model_copy(update={"detect_vendored_content": False}))
    assert len(off.findings) == 3                                                                      # the switch restores the old behaviour


def test_gitattributes_linguist_lines_become_globs():
    vendored, generated = linguist_globs("# comment\nthirdparty/ linguist-vendored\n/web/lib/* linguist-vendored=true\n*.pb.js linguist-generated\nkeep.js linguist-vendored=false\nother.js -linguist-vendored\n")
    assert "**/thirdparty" in vendored and "**/thirdparty/**" in vendored and "web/lib/*" in vendored
    assert generated == ("**/*.pb.js", "**/*.pb.js/**")
    assert not any("keep.js" in glob or "other.js" in glob for glob in vendored)
