//! Imports, resolved on the client. The server never receives an import string: each import goes as the ordered candidate paths that
//! exist among the request's files, and the server takes the first one it scored. This is a port of `krypton/engine/imports.py`
//! (extraction and candidate generation); `tests/wire.rs` pins it to the values the Python code gives.

#![allow(
    clippy::arithmetic_side_effects,
    clippy::string_slice,
    reason = "client side: offsets come from tree-sitter and from the length of the very text being sliced, so they sit on character boundaries and far below usize::MAX"
)]

use std::collections::BTreeSet;

use serde_json::Value;
use tree_sitter::Node;

use super::posix::{dirname, join, normpath};
use crate::error::WalkError;
use crate::lang::Lang;

const JS_EXTENSIONS: [&str; 6] = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"];

/// `from ..pkg.mod import a` -> level 2, module `pkg/mod`, names `a`; level 0 is absolute.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PythonImport {
    pub level: usize,
    pub module: String,
    pub names: Vec<String>,
}

/// What the tree says it imports, before any resolution.
#[derive(Debug, Default)]
pub struct RawImports {
    pub specifiers: Vec<String>,
    pub python: Vec<PythonImport>,
}

/// A TypeScript path alias: the pattern and the targets it may stand for.
pub type Alias = (String, Vec<String>);

/// The settings that decide where an import can lead (from the user's config overrides).
#[derive(Debug, Clone)]
pub struct ImportConfig {
    pub python_source_roots: Vec<String>,
    pub ts_path_aliases: Vec<Alias>,
    pub ts_base_url: Option<String>,
}

impl Default for ImportConfig {
    fn default() -> Self {
        Self { python_source_roots: vec![".".to_string(), "src".to_string()], ts_path_aliases: Vec::new(), ts_base_url: None }
    }
}

fn text_of<'s>(node: Node, source: &'s str) -> Result<&'s str, WalkError> {
    node.utf8_text(source.as_bytes()).map_err(WalkError::InvalidUtf8)
}

fn dotted_path(text: &str) -> String {
    text.replace('.', "/")
}

/// A string literal's text without its quotes; None for anything else.
fn string_specifier(node: Option<Node>, source: &str) -> Result<Option<String>, WalkError> {
    let Some(node) = node.filter(|found| found.kind() == "string") else { return Ok(None) };
    let text = text_of(node, source)?;
    Ok((text.chars().count() >= 2).then(|| text.chars().skip(1).take(text.chars().count() - 2).collect()))
}

/// `import x from "./a"` / `export * from "./a"`.
fn declaration_specifier(node: Node, source: &str) -> Result<Option<String>, WalkError> {
    string_specifier(node.child_by_field_name("source"), source)
}

/// `require("./a")` / `import("./a")`; template strings are skipped.
fn call_specifier(node: Node, source: &str) -> Result<Option<String>, WalkError> {
    let Some(function) = node.child_by_field_name("function") else { return Ok(None) };
    let is_require = function.kind() == "identifier" && text_of(function, source)? == "require";
    if function.kind() != "import" && !is_require {
        return Ok(None);
    }
    let first = node.child_by_field_name("arguments").and_then(|arguments| arguments.named_child(0));
    string_specifier(first, source)
}

/// The module-name node of an import clause, unwrapping `x as y`.
fn module_of(child: Node) -> Option<Node> {
    if child.kind() == "aliased_import" {
        child.child_by_field_name("name")
    } else {
        Some(child)
    }
}

fn python_imports(node: Node, source: &str) -> Result<Vec<PythonImport>, WalkError> {
    if node.kind() == "import_statement" {
        let mut found = Vec::new();
        let mut cursor = node.walk();
        for child in node.named_children(&mut cursor) {
            let module = module_of(child).map(|module| text_of(module, source)).transpose()?;
            found.extend(module.map(|text| PythonImport { level: 0, module: dotted_path(text), names: Vec::new() }));
        }
        return Ok(found);
    }
    let module_text = node.child_by_field_name("module_name").map(|module| text_of(module, source)).transpose()?.unwrap_or("");
    let level = module_text.len() - module_text.trim_start_matches('.').len();
    let mut names = Vec::new();
    let mut cursor = node.walk();
    for child in node.named_children(&mut cursor).skip(1) {
        if child.kind() == "wildcard_import" {
            continue;
        }
        if let Some(module) = module_of(child) {
            names.push(text_of(module, source)?.to_string());
        }
    }
    Ok(vec![PythonImport { level, module: dotted_path(&module_text[level..]), names }])
}

/// Read what one node imports, if it is an import statement or call, into `raw`.
///
/// # Errors
///
/// [`WalkError::InvalidUtf8`] when the import's text is not valid UTF-8.
pub fn observe(raw: &mut RawImports, node: Node, source: &str, lang: Lang) -> Result<(), WalkError> {
    let kind = node.kind();
    if lang == Lang::Python {
        if kind == "import_statement" || kind == "import_from_statement" {
            raw.python.extend(python_imports(node, source)?);
        }
        return Ok(());
    }
    let found = match kind {
        "import_statement" | "export_statement" => declaration_specifier(node, source)?,
        "call_expression" => call_specifier(node, source)?,
        _ => None,
    };
    raw.specifiers.extend(found);
    Ok(())
}

// ---------------------------------------------------------------- candidates

/// What `*` matched if the specifier fits the alias pattern ('' if exact), else None.
fn wildcard_match(pattern: &str, specifier: &str) -> Option<String> {
    let Some((prefix, suffix)) = pattern.split_once('*') else { return (pattern == specifier).then(String::new) };
    let fits = specifier.starts_with(prefix) && specifier.ends_with(suffix) && specifier.len() >= prefix.len() + suffix.len();
    fits.then(|| specifier[prefix.len()..specifier.len() - suffix.len()].to_string())
}

/// tsconfig `paths` patterns first (one `*` wildcard), then `baseUrl`.
fn alias_bases(specifier: &str, config: &ImportConfig) -> Vec<String> {
    let root = config.ts_base_url.as_deref().unwrap_or(".");
    let mut bases = Vec::new();
    for (pattern, targets) in &config.ts_path_aliases {
        if let Some(star) = wildcard_match(pattern, specifier) {
            bases.extend(targets.iter().map(|target| join(root, &target.replace('*', &star))));
        }
    }
    if let Some(base_url) = &config.ts_base_url {
        bases.push(join(base_url, specifier));
    }
    bases
}

/// Paths to try: as written, `.js` -> `.ts` / `.tsx`, each extension, then `/index.*`.
fn js_candidates(base: &str) -> Vec<String> {
    let mut candidates = vec![base.to_string()];
    if let Some(stem) = base.strip_suffix(".js") {
        candidates.extend([format!("{stem}.ts"), format!("{stem}.tsx")]);
    }
    candidates.extend(JS_EXTENSIONS.iter().map(|extension| format!("{base}{extension}")));
    candidates.extend(JS_EXTENSIONS.iter().map(|extension| join(base, &format!("index{extension}"))));
    candidates
}

fn js_choice(importer: &str, specifier: &str, config: &ImportConfig) -> Vec<String> {
    let bases =
        if specifier.starts_with("./") || specifier.starts_with("../") { vec![join(&dirname(importer), specifier)] } else { alias_bases(specifier, config) };
    bases.iter().flat_map(|base| js_candidates(&normpath(base))).collect()
}

/// One folder up per leading dot beyond the first.
fn relative_folder(importer: &str, level: usize) -> String {
    let mut folder = dirname(importer);
    for _ in 1..level {
        folder = dirname(&folder);
    }
    if folder.is_empty() {
        ".".to_string()
    } else {
        folder
    }
}

/// `mod.py` then `mod/__init__.py`; only `__init__.py` for a bare package.
fn python_candidates(module_dir: &str, is_module: bool) -> Vec<String> {
    let package = normpath(&join(module_dir, "__init__.py"));
    if is_module {
        vec![normpath(&format!("{module_dir}.py")), package]
    } else {
        vec![package]
    }
}

fn folder_choices(folder: &str, imported: &PythonImport) -> Vec<Vec<String>> {
    let module_dir = if imported.module.is_empty() { folder.to_string() } else { join(folder, &imported.module) };
    let mut choices = vec![python_candidates(&module_dir, !imported.module.is_empty())];
    choices.extend(imported.names.iter().map(|name| python_candidates(&join(&module_dir, &dotted_path(name)), true)));
    choices
}

fn python_choices(importer: &str, statement: &PythonImport, config: &ImportConfig) -> Vec<Vec<String>> {
    let folders = if statement.level > 0 { vec![relative_folder(importer, statement.level)] } else { config.python_source_roots.clone() };
    folders.iter().flat_map(|folder| folder_choices(folder, statement)).collect()
}

/// Every import as an ordered list of candidate paths, keeping only the candidates that are among `known` files; imports that
/// can reach none of the files are left out.
#[must_use]
pub fn candidates(importer: &str, raw: &RawImports, config: &ImportConfig, known: &BTreeSet<String>) -> Vec<Vec<String>> {
    let javascript = raw.specifiers.iter().map(|specifier| js_choice(importer, specifier, config));
    let python = raw.python.iter().flat_map(|imported| python_choices(importer, imported, config));
    javascript
        .chain(python)
        .map(|choice| choice.into_iter().filter(|candidate| known.contains(candidate)).collect::<Vec<_>>())
        .filter(|choice| !choice.is_empty())
        .collect()
}

fn string_items(value: &Value, name: &str) -> Result<Vec<String>, String> {
    let bad = || format!("{name} must be a list of strings");
    value.as_array().ok_or_else(bad)?.iter().map(|item| item.as_str().map(str::to_string).ok_or_else(bad)).collect()
}

impl ImportConfig {
    /// Read `python_source_roots`, `ts_path_aliases` and `ts_base_url` from the config overrides, if present.
    ///
    /// # Errors
    ///
    /// A message naming the setting when `python_source_roots` is not a list of strings, `ts_base_url` is not a string, `ts_path_aliases` is not an object, or one alias's targets are not a list of strings.
    pub fn from_overrides(overrides: &Value) -> Result<Self, String> {
        let mut config = Self::default();
        if let Some(roots) = overrides.get("python_source_roots") {
            config.python_source_roots = string_items(roots, "python_source_roots")?;
        }
        if let Some(base) = overrides.get("ts_base_url") {
            config.ts_base_url = base.as_str().map(str::to_string).map(Some).ok_or("ts_base_url must be a string")?;
        }
        if let Some(aliases) = overrides.get("ts_path_aliases") {
            let map = aliases.as_object().ok_or("ts_path_aliases must be an object")?;
            for (pattern, targets) in map {
                config.ts_path_aliases.push((pattern.clone(), string_items(targets, "ts_path_aliases")?));
            }
        }
        Ok(config)
    }
}
