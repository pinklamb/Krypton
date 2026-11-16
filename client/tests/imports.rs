//! The client-side import rules and waiver facts. The expected values were computed by the Python reference this client replaced, so the two
//! are pinned to the same answers; a private parity check compares them end to end.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::type_complexity,
    clippy::excessive_nesting,
    clippy::too_many_lines,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    reason = "a test may stop at the first surprise and index its own fixtures"
)]

use std::collections::BTreeSet;

use krypton_client::{
    dirname, expression_digest, import_candidates, normpath, posix_join, Builder, ClientConfig, ImportConfig, Names, PythonImport, RawImports,
};
use serde_json::Value;

#[test]
fn test_posix_join() {
    let table: &[(&str, &str, &str)] = &[
        ("a", "b", "a/b"),
        ("a/", "b", "a/b"),
        ("", "b", "b"),
        ("a", "/b", "/b"),
        ("a/b", "../c", "a/b/../c"),
        (".", "x", "./x"),
        ("", "", ""),
        ("a", "", "a/"),
    ];
    for (a, b, expected) in table {
        assert_eq!(posix_join(a, b), *expected, "join({a:?}, {b:?})");
    }
}

#[test]
fn test_posix_normpath() {
    let table: &[(&str, &str)] = &[
        ("a//b/../c", "a/c"),
        ("/", "/"),
        ("//x", "//x"),
        ("///x", "/x"),
        ("./", "."),
        ("", "."),
        ("../..", "../.."),
        ("a/b/", "a/b"),
        ("a/./b", "a/b"),
        ("../a/../..", "../.."),
        ("/..", "/"),
        ("a/../..", ".."),
        ("x/y/../../z", "z"),
        ("./a/b/../../..", ".."),
        ("/a/../../b", "/b"),
    ];
    for (path, expected) in table {
        assert_eq!(normpath(path), *expected, "normpath({path:?})");
    }
}

#[test]
fn test_posix_dirname() {
    let table: &[(&str, &str)] =
        &[("a/b/c.py", "a/b"), ("c.py", ""), ("/a", "/"), ("/", "/"), ("a//b", "a"), ("a/b/", "a/b"), ("//", "//"), ("", ""), ("x/", "x")];
    for (path, expected) in table {
        assert_eq!(dirname(path), *expected, "dirname({path:?})");
    }
}

type Imports = Vec<(usize, &'static str, &'static [&'static str])>;
type Choices = Vec<&'static [&'static str]>;

fn choices_table() -> Vec<(&'static str, &'static [&'static str], Imports, &'static str, Choices)> {
    vec![
        (
            "src/app/main.ts",
            &["./util", "../lib/x.js", "./dir", "../../up"],
            vec![],
            "{}",
            vec![
                &[
                    "src/app/util",
                    "src/app/util.ts",
                    "src/app/util.tsx",
                    "src/app/util.js",
                    "src/app/util.jsx",
                    "src/app/util.mjs",
                    "src/app/util.cjs",
                    "src/app/util/index.ts",
                    "src/app/util/index.tsx",
                    "src/app/util/index.js",
                    "src/app/util/index.jsx",
                    "src/app/util/index.mjs",
                    "src/app/util/index.cjs",
                ],
                &[
                    "src/lib/x.js",
                    "src/lib/x.ts",
                    "src/lib/x.tsx",
                    "src/lib/x.js.ts",
                    "src/lib/x.js.tsx",
                    "src/lib/x.js.js",
                    "src/lib/x.js.jsx",
                    "src/lib/x.js.mjs",
                    "src/lib/x.js.cjs",
                    "src/lib/x.js/index.ts",
                    "src/lib/x.js/index.tsx",
                    "src/lib/x.js/index.js",
                    "src/lib/x.js/index.jsx",
                    "src/lib/x.js/index.mjs",
                    "src/lib/x.js/index.cjs",
                ],
                &[
                    "src/app/dir",
                    "src/app/dir.ts",
                    "src/app/dir.tsx",
                    "src/app/dir.js",
                    "src/app/dir.jsx",
                    "src/app/dir.mjs",
                    "src/app/dir.cjs",
                    "src/app/dir/index.ts",
                    "src/app/dir/index.tsx",
                    "src/app/dir/index.js",
                    "src/app/dir/index.jsx",
                    "src/app/dir/index.mjs",
                    "src/app/dir/index.cjs",
                ],
                &[
                    "up",
                    "up.ts",
                    "up.tsx",
                    "up.js",
                    "up.jsx",
                    "up.mjs",
                    "up.cjs",
                    "up/index.ts",
                    "up/index.tsx",
                    "up/index.js",
                    "up/index.jsx",
                    "up/index.mjs",
                    "up/index.cjs",
                ],
            ],
        ),
        (
            "src/app/main.ts",
            &["@/a/b", "~lib", "pkg/x", "@/exact"],
            vec![],
            "{\"ts_path_aliases\": {\"@/*\": [\"src/*\", \"lib/*\"], \"~lib\": [\"lib/index\"], \"@/exact\": [\"src/exact.ts\"]}, \"ts_base_url\": \".\"}",
            vec![
                &[
                    "src/a/b",
                    "src/a/b.ts",
                    "src/a/b.tsx",
                    "src/a/b.js",
                    "src/a/b.jsx",
                    "src/a/b.mjs",
                    "src/a/b.cjs",
                    "src/a/b/index.ts",
                    "src/a/b/index.tsx",
                    "src/a/b/index.js",
                    "src/a/b/index.jsx",
                    "src/a/b/index.mjs",
                    "src/a/b/index.cjs",
                    "lib/a/b",
                    "lib/a/b.ts",
                    "lib/a/b.tsx",
                    "lib/a/b.js",
                    "lib/a/b.jsx",
                    "lib/a/b.mjs",
                    "lib/a/b.cjs",
                    "lib/a/b/index.ts",
                    "lib/a/b/index.tsx",
                    "lib/a/b/index.js",
                    "lib/a/b/index.jsx",
                    "lib/a/b/index.mjs",
                    "lib/a/b/index.cjs",
                    "@/a/b",
                    "@/a/b.ts",
                    "@/a/b.tsx",
                    "@/a/b.js",
                    "@/a/b.jsx",
                    "@/a/b.mjs",
                    "@/a/b.cjs",
                    "@/a/b/index.ts",
                    "@/a/b/index.tsx",
                    "@/a/b/index.js",
                    "@/a/b/index.jsx",
                    "@/a/b/index.mjs",
                    "@/a/b/index.cjs",
                ],
                &[
                    "lib/index",
                    "lib/index.ts",
                    "lib/index.tsx",
                    "lib/index.js",
                    "lib/index.jsx",
                    "lib/index.mjs",
                    "lib/index.cjs",
                    "lib/index/index.ts",
                    "lib/index/index.tsx",
                    "lib/index/index.js",
                    "lib/index/index.jsx",
                    "lib/index/index.mjs",
                    "lib/index/index.cjs",
                    "~lib",
                    "~lib.ts",
                    "~lib.tsx",
                    "~lib.js",
                    "~lib.jsx",
                    "~lib.mjs",
                    "~lib.cjs",
                    "~lib/index.ts",
                    "~lib/index.tsx",
                    "~lib/index.js",
                    "~lib/index.jsx",
                    "~lib/index.mjs",
                    "~lib/index.cjs",
                ],
                &[
                    "pkg/x",
                    "pkg/x.ts",
                    "pkg/x.tsx",
                    "pkg/x.js",
                    "pkg/x.jsx",
                    "pkg/x.mjs",
                    "pkg/x.cjs",
                    "pkg/x/index.ts",
                    "pkg/x/index.tsx",
                    "pkg/x/index.js",
                    "pkg/x/index.jsx",
                    "pkg/x/index.mjs",
                    "pkg/x/index.cjs",
                ],
                &[
                    "src/exact",
                    "src/exact.ts",
                    "src/exact.tsx",
                    "src/exact.js",
                    "src/exact.jsx",
                    "src/exact.mjs",
                    "src/exact.cjs",
                    "src/exact/index.ts",
                    "src/exact/index.tsx",
                    "src/exact/index.js",
                    "src/exact/index.jsx",
                    "src/exact/index.mjs",
                    "src/exact/index.cjs",
                    "lib/exact",
                    "lib/exact.ts",
                    "lib/exact.tsx",
                    "lib/exact.js",
                    "lib/exact.jsx",
                    "lib/exact.mjs",
                    "lib/exact.cjs",
                    "lib/exact/index.ts",
                    "lib/exact/index.tsx",
                    "lib/exact/index.js",
                    "lib/exact/index.jsx",
                    "lib/exact/index.mjs",
                    "lib/exact/index.cjs",
                    "src/exact.ts",
                    "src/exact.ts.ts",
                    "src/exact.ts.tsx",
                    "src/exact.ts.js",
                    "src/exact.ts.jsx",
                    "src/exact.ts.mjs",
                    "src/exact.ts.cjs",
                    "src/exact.ts/index.ts",
                    "src/exact.ts/index.tsx",
                    "src/exact.ts/index.js",
                    "src/exact.ts/index.jsx",
                    "src/exact.ts/index.mjs",
                    "src/exact.ts/index.cjs",
                    "@/exact",
                    "@/exact.ts",
                    "@/exact.tsx",
                    "@/exact.js",
                    "@/exact.jsx",
                    "@/exact.mjs",
                    "@/exact.cjs",
                    "@/exact/index.ts",
                    "@/exact/index.tsx",
                    "@/exact/index.js",
                    "@/exact/index.jsx",
                    "@/exact/index.mjs",
                    "@/exact/index.cjs",
                ],
            ],
        ),
        (
            "a.js",
            &["./b", "lodash"],
            vec![],
            "{\"ts_base_url\": \"src\"}",
            vec![
                &[
                    "b",
                    "b.ts",
                    "b.tsx",
                    "b.js",
                    "b.jsx",
                    "b.mjs",
                    "b.cjs",
                    "b/index.ts",
                    "b/index.tsx",
                    "b/index.js",
                    "b/index.jsx",
                    "b/index.mjs",
                    "b/index.cjs",
                ],
                &[
                    "src/lodash",
                    "src/lodash.ts",
                    "src/lodash.tsx",
                    "src/lodash.js",
                    "src/lodash.jsx",
                    "src/lodash.mjs",
                    "src/lodash.cjs",
                    "src/lodash/index.ts",
                    "src/lodash/index.tsx",
                    "src/lodash/index.js",
                    "src/lodash/index.jsx",
                    "src/lodash/index.mjs",
                    "src/lodash/index.cjs",
                ],
            ],
        ),
        (
            "pkg/sub/mod.py",
            &[],
            vec![(0, "os/path", &[]), (1, "", &["a", "b"]), (2, "util", &["x"]), (3, "y", &[]), (0, "pkg/sub", &["mod", "other"])],
            "{}",
            vec![
                &["os/path.py", "os/path/__init__.py"],
                &["src/os/path.py", "src/os/path/__init__.py"],
                &["pkg/sub/__init__.py"],
                &["pkg/sub/a.py", "pkg/sub/a/__init__.py"],
                &["pkg/sub/b.py", "pkg/sub/b/__init__.py"],
                &["pkg/util.py", "pkg/util/__init__.py"],
                &["pkg/util/x.py", "pkg/util/x/__init__.py"],
                &["y.py", "y/__init__.py"],
                &["pkg/sub.py", "pkg/sub/__init__.py"],
                &["pkg/sub/mod.py", "pkg/sub/mod/__init__.py"],
                &["pkg/sub/other.py", "pkg/sub/other/__init__.py"],
                &["src/pkg/sub.py", "src/pkg/sub/__init__.py"],
                &["src/pkg/sub/mod.py", "src/pkg/sub/mod/__init__.py"],
                &["src/pkg/sub/other.py", "src/pkg/sub/other/__init__.py"],
            ],
        ),
        (
            "mod.py",
            &[],
            vec![(0, "a", &["b.c"]), (1, "z", &[])],
            "{\"python_source_roots\": [\"lib\", \".\"]}",
            vec![
                &["lib/a.py", "lib/a/__init__.py"],
                &["lib/a/b/c.py", "lib/a/b/c/__init__.py"],
                &["a.py", "a/__init__.py"],
                &["a/b/c.py", "a/b/c/__init__.py"],
                &["z.py", "z/__init__.py"],
            ],
        ),
    ]
}

fn raw_of(specifiers: &[&str], python: &Imports) -> RawImports {
    RawImports {
        specifiers: specifiers.iter().map(ToString::to_string).collect(),
        python: python
            .iter()
            .map(|(level, module, names)| PythonImport { level: *level, module: module.to_string(), names: names.iter().map(ToString::to_string).collect() })
            .collect(),
    }
}

#[test]
fn test_import_choices() {
    for (importer, specifiers, python, config, expected) in choices_table() {
        let overrides: Value = serde_json::from_str(config).expect("config is JSON");
        let settings = ImportConfig::from_overrides(&overrides).expect("valid settings");
        let known: BTreeSet<String> = expected.iter().flat_map(|choice| choice.iter().map(ToString::to_string)).collect();
        let got = import_candidates(importer, &raw_of(specifiers, &python), &settings, &known);
        let wanted: Vec<Vec<String>> = expected.iter().map(|choice| choice.iter().map(ToString::to_string).collect()).collect();
        assert_eq!(got, wanted, "{importer}");
    }
}

#[test]
fn test_only_existing_files() {
    let raw = raw_of(&["./util"], &Vec::new());
    let known: BTreeSet<String> = ["src/app/util.ts", "src/app/other.ts"].iter().map(ToString::to_string).collect();
    let got = import_candidates("src/app/main.ts", &raw, &ImportConfig::default(), &known);
    assert_eq!(got, vec![vec!["src/app/util.ts".to_string()]]);
    assert_eq!(import_candidates("src/app/main.ts", &raw, &ImportConfig::default(), &BTreeSet::new()), Vec::<Vec<String>>::new());
}

#[test]
fn test_bad_import_config() {
    for bad in [r#"{"python_source_roots": "src"}"#, r#"{"ts_base_url": 3}"#, r#"{"ts_path_aliases": []}"#, r#"{"ts_path_aliases": {"@/*": "src"}}"#] {
        let overrides: Value = serde_json::from_str(bad).expect("JSON");
        assert!(ImportConfig::from_overrides(&overrides).is_err(), "{bad}");
    }
}

type WaiverRows = Vec<(usize, bool, &'static str)>;

fn waiver_table() -> Vec<(&'static str, WaiverRows)> {
    vec![
        ("# krypton: ignore -- legacy thing\ndef f(): pass\n", vec![(1, true, "legacy thing")]),
        ("x = 1  # krypton: ignore\n", vec![(1, false, "")]),
        ("// krypton: ignore: because\nfunction f(){}\n", vec![(1, true, "because")]),
        ("/* krypton: ignore -- block */\n", vec![(1, true, "block")]),
        ("a = 1\n   # krypton: ignore --   spaced out reason  \n#krypton:ignore -x\n# not a waiver\n", vec![(2, true, "spaced out reason"), (3, true, "x")]),
        ("# krypton: ignore -- one\n# krypton: ignore -- two\n", vec![(1, true, "one"), (2, true, "two")]),
        ("\u{a0}# krypton: ignore -- nbsp\n", vec![(1, true, "nbsp")]),
        ("x = '#' # krypton: ignorefoo\n", vec![]),
    ]
}

#[test]
fn test_waiver_facts() {
    let builder = Builder::new(ClientConfig::from_overrides(Value::Null).expect("no overrides")).expect("patterns compile");
    for (text, expected) in waiver_table() {
        let mut names = Names::new();
        let file = builder.file("a.py", text.as_bytes(), &BTreeSet::new(), &mut names).expect("a file");
        let mut got: Vec<(usize, bool, Option<String>)> =
            file.waivers.iter().map(|(line, waiver)| (line.parse().expect("number"), waiver.comment_only, waiver.reason.clone())).collect();
        got.sort();
        let wanted: Vec<(usize, bool, Option<String>)> =
            expected.iter().map(|(line, alone, reason)| (*line, *alone, (!reason.is_empty()).then(|| expression_digest(reason)))).collect();
        assert_eq!(got, wanted, "{text:?}");
        for (_, _, reason) in &expected {
            if !reason.is_empty() {
                assert_eq!(names.get(&expression_digest(reason)).map(String::as_str), Some(*reason), "the client keeps the reason");
            }
        }
    }
}
