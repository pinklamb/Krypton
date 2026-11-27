//! The call graph of a repository, built on this machine from the source: every function, and for each one the functions that call it.
//! Nothing here is sent anywhere and nothing here affects a score; `krypton impact` uses it to show what a change reaches downstream.
//!
//! A call is tied to a function only on evidence, most certain first: a method of the caller's own class or a class it extends
//! (`self.x()`, `this.x()`); a method of the class an object holds, when the code states it (`self.store = Store()`, `store: Store`, see
//! `holders`); a function of the same file or of a file it imports (a Python script's sibling modules included); a module's function or
//! class, or a class's method, called on its name; a class being created. A method on an object whose class the code does not state
//! (`order.charge()`) is tied only when exactly one method of that name is in sight, and the link is marked as resting on the name alone.
//! Everything else (other libraries, built-ins, calls decided at run time) is left unresolved and counted by method name, never guessed.
//!
//! The graph is a petgraph `DiGraph`. Test files are marked by the scorer's own test patterns, so `krypton impact` can keep them apart.

mod changes;
mod extract;
mod fingerprint;
mod graph;
mod holders;
mod resolve;
mod routes;

use std::collections::BTreeSet;

use krypton_client::{dirname, import_candidates, parse, walk_events, Event, ImportConfig, Lang, WalkError};
use tree_sitter::Node;

pub use changes::{compare, Change, Changes};
pub use graph::{Function, Graph};

/// One file, as the graph needs it: its path, what it defines and calls, and the files its imports lead to.
pub struct Parsed {
    pub path: String,
    pub calls: extract::FileCalls,
    pub imports: Vec<String>,
    /// The 1-based line of the first syntax error, when the file does not parse cleanly (tree-sitter still gives a partial tree).
    pub syntax_error: Option<u32>,
}

/// The 1-based line of the first syntax error (an ERROR or missing node) in a tree, if it has one.
fn first_error(root: Node<'_>) -> Option<u32> {
    if !root.has_error() {
        return None;
    }
    let mut found = None;
    walk_events(root, |event| {
        if let (Event::Enter(node, _), None) = (event, found) {
            if node.is_error() || node.is_missing() {
                found = u32::try_from(node.start_position().row.saturating_add(1)).ok();
            }
        }
    });
    found.or(Some(1))
}

/// Whether a path is a test file, by the scorer's own test patterns (`engine/src/score/settings.rs`, `TEST_GLOBS`): `test_*.py`,
/// `*_test.py`, `conftest.py`, anything under a `tests` or `__tests__` folder, and `*.test.*` / `*.spec.*`.
#[must_use]
pub fn is_test_path(path: &str) -> bool {
    let mut folders = path.split('/').collect::<Vec<_>>();
    let file = folders.pop().unwrap_or(path);
    let python = std::path::Path::new(file).extension().is_some_and(|extension| extension.eq_ignore_ascii_case("py"))
        && (file.starts_with("test_") || file.ends_with("_test.py") || file == "conftest.py");
    python || folders.iter().any(|folder| *folder == "tests" || *folder == "__tests__") || file.contains(".test.") || file.contains(".spec.")
}

/// Where a file's imports are looked for. A Python file also looks in its own folder: run as a script, Python puts that folder first on
/// its path, so `import scanner` next to `scanner.py` finds it. The scorer does not do this (its import rules stay as they are); here it
/// keeps sibling modules from looking unused.
fn config_for(path: &str, lang: Lang, base: &ImportConfig) -> ImportConfig {
    let mut config = base.clone();
    let folder = dirname(path);
    if lang == Lang::Python && !folder.is_empty() && !config.python_source_roots.contains(&folder) {
        config.python_source_roots.push(folder);
    }
    config
}

/// Read every file's functions and calls, resolve its imports among the files given, and build the graph.
///
/// # Errors
///
/// The [`WalkError`] of the first file that cannot be read as its language: an unsupported extension, or a parser failure.
pub fn from_sources(files: &[(String, String)]) -> Result<Graph, WalkError> {
    let known: BTreeSet<String> = files.iter().map(|(path, _)| path.clone()).collect();
    let config = ImportConfig::default();
    let mut parsed = Vec::with_capacity(files.len());
    for (path, source) in files {
        let lang = Lang::from_path(path)?;
        let tree = parse(lang, source)?;
        let calls = extract::read_file(tree.root_node(), source, lang)?;
        let syntax_error = first_error(tree.root_node());
        // each import is a list of candidate files in the order its language tries them; the first that exists is the one it means
        let choices = import_candidates(path, &calls.imports, &config_for(path, lang, &config), &known);
        let imports = choices.into_iter().filter_map(|choice| choice.into_iter().next()).collect();
        parsed.push(Parsed { path: path.clone(), calls, imports, syntax_error });
    }
    Ok(Graph::new(&parsed))
}

#[cfg(test)]
mod tests {
    use super::*;

    // The scorer's test patterns.
    #[test]
    fn test_test_paths() {
        let tests = ["test_app.py", "pkg/app_test.py", "conftest.py", "tests/helpers.py", "web/__tests__/a.ts", "a.test.tsx", "b.spec.js"];
        let code = ["app.py", "testing.py", "contest.py", "latest/app.py", "spec.ts"];
        assert!(tests.iter().all(|path| is_test_path(path)), "tests");
        assert!(!code.iter().any(|path| is_test_path(path)), "code");
    }

    // A Python script imports a module next to it by its bare name, as Python does when it runs the script.
    #[test]
    fn test_sibling_import() {
        let files = [
            ("dashboard/scanner.py", "def analyze(repo):\n    return repo\n"),
            ("dashboard/server.py", "import scanner\n\ndef handle(repo):\n    return scanner.analyze(repo)\n"),
        ];
        let graph = from_sources(&files.map(|(path, source)| (path.to_string(), source.to_string()))).unwrap();
        let analyze = graph.find("dashboard/scanner.py::analyze").unwrap();
        let callers: Vec<String> = graph.callers(analyze).into_iter().filter_map(|(caller, _)| graph.function(caller).map(Function::key)).collect();
        assert_eq!(callers, ["dashboard/server.py::handle"], "the sibling import is followed");
    }
}
