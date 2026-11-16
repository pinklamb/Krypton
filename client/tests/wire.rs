//! The wire serializer. The expected values in the tables were computed by the Python reference this client replaced, so the two
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

use krypton_client::{
    collapsed, expression_digest, line_shapes, longest_line, parse, split_lines, walk_events, Builder, CharIndex, ClientConfig, Lang, Names, Vendored,
    WalkError, WireError, WireFile,
};
use std::collections::BTreeSet;

use serde_json::{json, Value};

fn builder() -> Builder {
    Builder::new(ClientConfig::from_overrides(Value::Null).expect("no overrides")).expect("patterns compile")
}

/// Build one file the way the command does for a single-file request: no other files, so no imports resolve.
fn file_of(builder: &Builder, path: &str, bytes: &[u8]) -> Result<WireFile, WireError> {
    builder.file(path, bytes, &BTreeSet::new(), &mut Names::new())
}

fn wire(path: &str, source: &str) -> WireFile {
    file_of(&builder(), path, source.as_bytes()).expect("a supported file")
}

const DIGESTS: &[(&str, &str, &str)] = &[
    ("a  b", "a b", "c8687a08aa5d6ed2"),
    ("x.y(1,\n 2)", "x.y(1, 2)", "c991a647a0289118"),
    ("\u{e9}\u{e9}", "\u{e9}\u{e9}", "f13c007a1d8e6e13"),
    ("a\u{1c}b  c", "a b c", "0e9f64031fcb2bc7"),
    ("", "", "e3b0c44298fc1c14"),
    ("None", "None", "dc937b59892604f5"),
    (" \u{a0}spaced\u{2003}out\u{3000}", "spaced out", "b0399288d6f3a476"),
    ("f(\n  a,\n  b\n)", "f( a, b )", "648b89c066f619fd"),
    ("\u{85}lead", "lead", "e3456bc1f4d270f4"),
    ("tab\tsep", "tab sep", "c731809e18f7962b"),
];

#[test]
fn test_collapsed() {
    for (text, expected, _) in DIGESTS {
        assert_eq!(collapsed(text), *expected, "{text:?}");
    }
}

#[test]
fn test_digest() {
    for (text, _, expected) in DIGESTS {
        assert_eq!(expression_digest(text), *expected, "{text:?}");
    }
}

const SPLITS: &[(&str, &[&str])] = &[
    ("a\nb", &["a", "b"]),
    ("a\r\nb\r\n", &["a", "b"]),
    ("a\rb", &["a", "b"]),
    ("a\u{b}b\u{c}c", &["a", "b", "c"]),
    ("a\u{1c}b\u{1d}c\u{1e}d", &["a", "b", "c", "d"]),
    ("a\u{85}b", &["a", "b"]),
    ("a\u{2028}b\u{2029}c", &["a", "b", "c"]),
    ("", &[]),
    ("\n", &[""]),
    ("a\n\nb", &["a", "", "b"]),
    ("no end", &["no end"]),
    ("x\r", &["x"]),
];

#[test]
fn test_split_lines() {
    for (text, expected) in SPLITS {
        assert_eq!(split_lines(text), *expected, "{text:?}");
    }
}

#[test]
fn test_longest_line() {
    assert_eq!(longest_line("ab\ncdef\r\ng"), 4);
    assert_eq!(longest_line("a\u{2028}bcd"), 3);
    assert_eq!(longest_line(""), 0);
    assert_eq!(longest_line("\u{1f600}\u{1f600}\n"), 2);
}

type Runs = Vec<(usize, Vec<(usize, usize)>)>;

fn shapes_table() -> Vec<(&'static str, Runs, Vec<usize>, bool)> {
    vec![
        ("x = 1\n", vec![(5, vec![(0, 1), (2, 3), (4, 5)])], vec![], true),
        ("a  b  \r\nc\r\n", vec![(6, vec![(0, 1), (3, 4)]), (1, vec![(0, 1)])], vec![0, 1], true),
        ("", vec![(0, vec![])], vec![], false),
        ("\n", vec![(0, vec![])], vec![], true),
        ("  \tx\t y  \n z", vec![(9, vec![(3, 4), (6, 7)]), (2, vec![(1, 2)])], vec![], false),
        ("\u{e9} \u{1f600} q\n", vec![(5, vec![(0, 1), (2, 3), (4, 5)])], vec![], true),
        ("a\u{1c}b c\n", vec![(5, vec![(0, 1), (2, 3), (4, 5)])], vec![], true),
        ("last", vec![(4, vec![(0, 4)])], vec![], false),
    ]
}

#[test]
fn test_line_shapes() {
    for (text, lines, cr, final_newline) in shapes_table() {
        let shapes = line_shapes(text);
        assert_eq!((shapes.lines, shapes.cr, shapes.final_newline), (lines, cr, final_newline), "{text:?}");
    }
}

fn vendored_table() -> Vec<(&'static str, String, Option<&'static str>)> {
    vec![
        ("static/chart.js", "var a=1;".repeat(700), Some("vendored_name")),
        ("lib/jquery-3.6.0.min.js", format!("/*! jQuery v3.6.0 | (c) OpenJS */\n{}", "x".repeat(3000)), Some("vendored_name")),
        ("src/app.js", "function f(){ return 1 }\n".to_string(), None),
        ("assets/app.bundle.js", "/*! @license MIT v1.2.3 */\n__webpack_require__(1);\n".to_string(), Some("vendored_content")),
        ("dist/main.js", format!("var a=1;\n//# sourceMappingURL=main.js.map\n{}", "b".repeat(2100)), None),
        ("vendor/react-dom.production.min.js", "x".repeat(100), None),
        ("src/lib/chart.js", "/* Copyright (c) 2020 Acme, Licensed under MIT v2.0.1 */\nexport const x = 1;\n".to_string(), None),
        ("a.py", "x = 1\n".to_string(), None),
        ("static/site.js", format!("(function(){{{}}})()", "a;".repeat(1200)), Some("vendored_content")),
        ("public/bootstrap.js", "x = 1;\n".to_string(), Some("vendored_name")),
        ("lib/Backbone-1.4.min.JS", "y".to_string(), Some("vendored_name")),
        ("x/static/public/other.js", format!("var q = 'a'.repeat(5000); {}", "z".repeat(1200)), None),
    ]
}

#[test]
fn test_vendored() {
    let rules = Vendored::new().expect("patterns compile");
    for (name, text, expected) in vendored_table() {
        assert_eq!(rules.reason(name, &text), expected, "{name}");
    }
}

#[test]
fn test_char_columns() {
    let text = "x = '\u{1f600}' + yy\ny = '\u{e9}' + zz\n";
    let chars = CharIndex::new(text);
    let yy = text.find("yy").expect("yy is there");
    let zz = text.find("zz").expect("zz is there");
    assert_eq!((chars.index(yy), chars.column(0, yy)), (10, 10));
    assert_eq!((chars.index(zz), chars.column(1, zz)), (23, 10));
    assert_eq!(chars.index(text.len()), u32::try_from(text.chars().count()).unwrap());
}

/// Every node of the tree is on the wire: the count equals what an independent walk of the same tree sees.
fn assert_complete(path: &str, source: &str) {
    let file = wire(path, source);
    let lang = Lang::from_path(path).expect("supported");
    let stats = walk_events(parse(lang, source).expect("parses").root_node(), |_| {});
    assert_eq!(file.tree.n, stats.nodes, "{path}");
    assert_eq!(file.tree.parent[0], -1, "{path}: the root has no parent");
    assert!(file.tree.parent.iter().enumerate().skip(1).all(|(index, &parent)| (0..i64::try_from(index).unwrap()).contains(&parent)), "{path}");
}

#[test]
fn test_every_node() {
    assert_complete("a.py", "def f(a):\n    if a:\n        return (a + 1)\n    return [x for x in a]  # note\n");
    assert_complete("a.js", "function f(a) { if (a) { return a?.b ?? 1; } /* c */ return `t${a}`; }\n");
    assert_complete("a.py", "");
    assert_complete("a.py", "def (:\n  x = = 1\n)))\n");
    assert_complete("a.js", "function f(a) {\n  if (a) {\n");
    assert_complete("a.py", "x = '\u{1f600}'  # \u{65e5}\u{672c}\u{8a9e}\n");
}

fn kinds_of(file: &WireFile) -> Vec<&str> {
    file.tree.kind.iter().map(|&number| file.tree.kinds[number as usize].as_str()).collect()
}

#[test]
fn test_punctuation_sent() {
    let file = wire("a.py", "def f(a):\n    return a\n");
    let kinds = kinds_of(&file);
    for token in ["def", "(", ")", ":", "return"] {
        assert!(kinds.contains(&token), "{token} missing from {kinds:?}");
    }
}

#[test]
fn test_no_text() {
    let source = "def calc(a):\n    secret = 'hunter2 is the password'  # TOP-SECRET-NOTE\n    return a + 1\n";
    let mut names = Names::new();
    let file = builder().file("a.py", source.as_bytes(), &BTreeSet::new(), &mut names).expect("file");
    let sent = serde_json::to_string(&file).expect("serializes");
    for word in ["calc", "secret", "hunter2", "TOP-SECRET-NOTE", "password"] {
        assert!(!sent.contains(word), "{word} reached the request");
    }
    assert_eq!(names.get(&expression_digest("calc")).map(String::as_str), Some("calc"), "the client keeps the names");
    assert!(file.tree.digest.values().any(|digest| *digest == expression_digest("secret")));
}

#[test]
fn test_digest_not_code() {
    let file = wire("a.py", "def f(a):\n    return a.compute(1, 'private value')\n");
    assert!(!file.tree.digest.is_empty());
    assert!(file.tree.digest.values().all(|digest| digest.len() == 16));
    let everything = serde_json::to_string(&file.tree).expect("serializes");
    assert!(!everything.contains("private value") && !everything.contains("compute"), "returned code leaked");
}

#[test]
fn test_masked_pattern() {
    let file = wire("a.py", "match x:\n    case 1:\n        pass\n    case _:\n        pass\n");
    let mut masked: Vec<&str> = file
        .tree
        .mask
        .iter()
        .filter(|(index, _)| kinds_of(&file)[index.parse::<usize>().expect("index")] == "case_pattern")
        .map(|(_, text)| text.as_str())
        .collect();
    masked.sort_unstable();
    assert_eq!(masked, ["?", "_"]);
}

#[test]
fn test_facts() {
    let file = wire("pkg/a.py", "# @generated\n# krypton: ignore -- legacy\ndef f(a):\n    return a\n");
    assert!(file.flags.generated_marker);
    assert_eq!(file.waivers.keys().collect::<Vec<_>>(), ["2"]);
    assert!(file.waivers["2"].comment_only);
    assert_eq!(file.waivers["2"].reason, Some(expression_digest("legacy")));
    assert_eq!((file.path.as_str(), file.language, file.bytes), ("pkg/a.py", "python", 64));
    assert_eq!(wire("a\\b.py", "x = 1\n").path, "a/b.py");
}

#[test]
fn test_bom_and_bytes() {
    let plain = file_of(&builder(), "a.py", b"x = 1\n").expect("file");
    let with_bom = file_of(&builder(), "a.py", b"\xef\xbb\xbfx = 1\n").expect("file");
    assert_eq!(plain.lines, with_bom.lines);
    assert_eq!((plain.bytes, with_bom.bytes), (6, 9));
    let broken = file_of(&builder(), "a.py", b"x = '\xff'\n").expect("invalid bytes are replaced, not refused");
    assert_eq!(broken.lines[0].0, 7);
}

#[test]
fn test_uppercase_extension() {
    assert_eq!(wire("A.PY", "x = 1\n").language, "python");
    assert_eq!(wire("b.MJS", "let a = 1;\n").language, "javascript");
}

#[test]
fn test_unsupported_file() {
    let error = file_of(&builder(), "a.rs", b"fn main() {}").expect_err("not supported");
    assert!(matches!(error, WireError::Walk(WalkError::UnsupportedFile(ref path)) if path == "a.rs"), "{error}");
    assert!(file_of(&builder(), "a.go", b"package main").is_err());
    for typed in ["a.ts", "a.mts", "a.cts", "a.tsx"] {
        assert!(file_of(&builder(), typed, b"let a: number = 1;").is_ok(), "{typed}");
    }
}

#[test]
fn test_bad_config() {
    for bad in [json!([1]), json!({"generated_markers": "x"}), json!({"generated_markers": [1]}), json!({"detect_vendored_content": "yes"})] {
        let error = ClientConfig::from_overrides(bad).err().expect("refused");
        assert!(matches!(error, WireError::Config(_)));
    }
}

#[test]
fn test_config_markers() {
    let config = ClientConfig::from_overrides(json!({"generated_markers": ["MAGIC"], "detect_vendored_content": false})).expect("valid");
    let custom = Builder::new(config).expect("patterns compile");
    let file = file_of(&custom, "static/chart.js", b"// MAGIC\nvar a=1;").expect("file");
    assert!(file.flags.generated_marker);
    assert_eq!(file.flags.vendored, None, "vendored detection was switched off");
}

#[test]
fn test_request_shape() {
    let builder = builder();
    let files = vec![file_of(&builder, "a.py", b"x = 1\n").expect("file")];
    let request = serde_json::to_value(builder.request(files, "strict", "full")).expect("serializes");
    assert_eq!(request["wire_version"], 1);
    assert_eq!(request["options"], json!({"level": "strict", "detail": "full", "config": {}}));
    let file = &request["files"][0];
    let mut keys: Vec<&str> = file.as_object().expect("object").keys().map(String::as_str).collect();
    keys.sort_unstable();
    assert_eq!(
        keys,
        [
            "altered_lines",
            "bytes",
            "change_frequency",
            "cr",
            "final_newline",
            "flags",
            "imports",
            "language",
            "lines",
            "longest_line",
            "path",
            "tree",
            "waivers"
        ]
    );
    assert_eq!(file["tree"]["n"], file["tree"]["kind"].as_array().expect("array").len());
}
