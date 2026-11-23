//! What a scan covered. Every file under the scanned folder is either scored or named here with the reason it was not, so an agent
//! reading the review can say for certain what was checked: source files the ignore rules leave out (and which rule), dependency and
//! build folders, kinds of file Krypton does not read (stylesheets, HTML, Rust, ...), files unchanged since `--base`, and files the
//! server set aside (a bundled library, generated code, a file too large). Worked out on this machine from the same walk the scan uses;
//! nothing here is sent anywhere.

use std::collections::{BTreeMap, HashSet};
use std::path::{Path, PathBuf};

use serde::Serialize;
use serde_json::Value;

use crate::scan_command::{is_source, KRYPTON_IGNORE, SKIP_DIRS};
use crate::Failure;

/// How many paths one line of the text report names before "and N more"; `--json` always has them all.
const NAMED: usize = 12;

/// A path that was not scored, and why. A folder's path ends in `/` and says how many source files it holds.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LeftOut {
    pub path: String,
    pub reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_files: Option<usize>,
}

/// Everything a scan did and did not score.
#[derive(Debug, Default, Serialize)]
pub struct Coverage {
    /// Source files the server scored.
    pub scored: usize,
    /// Source files (or whole folders of them) the ignore rules left out.
    pub ignored: Vec<LeftOut>,
    /// Dependency and build folders, never read (`node_modules/`, `target/`, ...).
    pub dependency_folders: Vec<String>,
    /// Files of a kind Krypton does not read, counted by extension.
    pub not_read: BTreeMap<String, usize>,
    /// With `--base`: source files left out because they did not change.
    pub unchanged: usize,
    /// Files the server set aside, with its reason.
    pub set_aside: Vec<LeftOut>,
    /// Files scored only as far as they parsed.
    pub parsed_with_errors: Vec<String>,
    /// Anything else the server could not score, by error code.
    pub server_errors: BTreeMap<String, String>,
}

/// The walker the scan uses: the repository's ignore rules (with or without `.kryptonignore`), dependency folders never entered.
#[must_use]
pub fn rule_walker(root: &Path, krypton_rules: bool) -> ignore::Walk {
    let mut builder = ignore::WalkBuilder::new(root);
    builder.hidden(false).git_global(false).require_git(false).filter_entry(|entry| !is_dependency_folder(entry.path()));
    if krypton_rules {
        builder.add_custom_ignore_filename(KRYPTON_IGNORE);
    }
    builder.build()
}

fn is_dependency_folder(path: &Path) -> bool {
    path.is_dir() && path.file_name().is_some_and(|name| SKIP_DIRS.contains(&name.to_string_lossy().as_ref()))
}

/// Every path under `root` with no ignore rules at all; a dependency folder is yielded but not entered.
fn raw_walker(root: &Path) -> ignore::Walk {
    let base = root.to_path_buf();
    ignore::WalkBuilder::new(root).standard_filters(false).filter_entry(move |entry| !inside_dependency_folder(&base, entry.path())).build()
}

fn inside_dependency_folder(root: &Path, path: &Path) -> bool {
    let Some(Ok(relative)) = path.parent().map(|parent| parent.strip_prefix(root)) else {
        return false;
    };
    relative.components().any(|part| SKIP_DIRS.contains(&part.as_os_str().to_string_lossy().as_ref()))
}

pub fn walk_failure(root: &Path, error: &ignore::Error) -> Failure {
    Failure::Read(root.display().to_string(), std::io::Error::other(error.to_string()))
}

/// Every path a walker yields.
fn visited(walk: ignore::Walk, root: &Path) -> Result<HashSet<PathBuf>, Failure> {
    walk.map(|entry| entry.map(ignore::DirEntry::into_path).map_err(|error| walk_failure(root, &error))).collect()
}

/// A path relative to the scanned folder, with forward slashes.
fn relative(root: &Path, path: &Path) -> String {
    path.strip_prefix(root).unwrap_or(path).to_string_lossy().replace('\\', "/")
}

/// The ignore rules as two sets of what they keep: with `.kryptonignore`, and with `.gitignore` alone (to tell which rule left a path out).
struct Rules {
    kept: HashSet<PathBuf>,
    git_kept: HashSet<PathBuf>,
}

/// The outermost path between `root` and `path` that the rules left out: the folder that was ignored as a whole, or the file itself.
fn outermost_ignored(root: &Path, path: &Path, kept: &HashSet<PathBuf>) -> PathBuf {
    let mut chain: Vec<&Path> = path.ancestors().take_while(|step| step.starts_with(root) && *step != root).collect();
    chain.reverse();
    chain.into_iter().find(|step| !kept.contains(*step)).unwrap_or(path).to_path_buf()
}

impl Coverage {
    /// What the ignore rules, the dependency folders and the kinds of file leave out under `root`.
    ///
    /// # Errors
    ///
    /// [`Failure::Read`] when the folder cannot be walked.
    pub fn survey(root: &Path) -> Result<Self, Failure> {
        let rules = Rules { kept: visited(rule_walker(root, true), root)?, git_kept: visited(rule_walker(root, false), root)? };
        let mut coverage = Self::default();
        let mut ignored: BTreeMap<String, LeftOut> = BTreeMap::new();
        for entry in raw_walker(root) {
            let path = entry.map_err(|error| walk_failure(root, &error))?.into_path();
            coverage.sort_out(root, &path, &rules, &mut ignored);
        }
        coverage.ignored = ignored.into_values().collect();
        Ok(coverage)
    }

    /// Put one path from the unfiltered walk where it belongs.
    fn sort_out(&mut self, root: &Path, path: &Path, rules: &Rules, ignored: &mut BTreeMap<String, LeftOut>) {
        if rules.kept.contains(path) {
            self.count_kind(path);
            return;
        }
        if is_dependency_folder(path) {
            self.note_dependency_folder(root, path);
            return;
        }
        if path.is_file() && is_source(path) {
            add_ignored(root, path, rules, ignored);
        }
    }

    /// A kept file Krypton does not read, counted under its extension.
    fn count_kind(&mut self, path: &Path) {
        if !path.is_file() || is_source(path) {
            return;
        }
        let kind = path.extension().map_or_else(|| "(no extension)".to_string(), |extension| format!(".{}", extension.to_string_lossy().to_lowercase()));
        let count = self.not_read.entry(kind).or_default();
        *count = count.saturating_add(1);
    }

    fn note_dependency_folder(&mut self, root: &Path, path: &Path) {
        if path.file_name().is_some_and(|name| name != ".git") {
            self.dependency_folders.push(format!("{}/", relative(root, path)));
        }
    }

    /// What the server says it scored, set aside or could not parse; `found` source files were found, `sent` of them were sent.
    pub fn add_answer(&mut self, result: &Value, found: usize, sent: usize) {
        let list = |key: &str| result.get(key).and_then(Value::as_array).cloned().unwrap_or_default();
        let text = |item: &Value, key: &str| item.get(key).and_then(Value::as_str).unwrap_or_default().to_string();
        let files = list("files");
        self.scored = files.len();
        self.unchanged = found.saturating_sub(sent);
        self.parsed_with_errors =
            files.iter().filter(|file| file.get("parse_error").and_then(Value::as_bool) == Some(true)).map(|file| text(file, "filename")).collect();
        self.set_aside = list("skipped_files")
            .iter()
            .map(|skip| LeftOut { path: text(skip, "filename"), reason: text(skip, "reason").replace('_', " "), source_files: None })
            .collect();
        self.server_errors = result
            .get("errors")
            .and_then(Value::as_object)
            .into_iter()
            .flatten()
            .map(|(code, message)| (code.clone(), message.as_str().map_or_else(|| message.to_string(), str::to_string)))
            .collect();
    }

    /// How many source files were found but not scored.
    #[must_use]
    pub fn sources_left_out(&self) -> usize {
        let ignored: usize = self.ignored.iter().map(|left| left.source_files.unwrap_or(1)).sum();
        ignored.saturating_add(self.set_aside.len()).saturating_add(self.unchanged)
    }

    /// The "What was scored" section of the full report (Markdown), for an agent or CI to read with the review.
    #[must_use]
    pub fn markdown(&self) -> String {
        let lines = self.left_out_lines();
        let mut text = format!("### What was scored\n\nScored {} (Python, JavaScript and TypeScript).", count(self.scored, "source file", "source files"));
        if lines.is_empty() {
            text.push_str(" Nothing was left out.\n");
            return text;
        }
        text.push_str(" Not scored:\n\n");
        for line in &lines {
            text.push_str("- ");
            text.push_str(line);
            text.push('\n');
        }
        text
    }

    /// One line of the left-out list per reason, most important first.
    fn left_out_lines(&self) -> Vec<String> {
        let mut lines: Vec<String> = self.set_aside.iter().map(|skip| format!("`{}`: set aside by the server ({})", skip.path, skip.reason)).collect();
        lines.extend(self.server_errors.iter().map(|(code, message)| format!("{code}: {message}")));
        lines.extend(self.parsed_with_errors.iter().map(|path| format!("`{path}`: did not fully parse, so only the parts that did were scored")));
        lines.extend(by_rule(&self.ignored));
        if self.unchanged > 0 {
            lines.push(format!("{} unchanged since the base ref (`--base`)", count(self.unchanged, "source file", "source files")));
        }
        if !self.dependency_folders.is_empty() {
            lines.push(format!(
                "dependency and build folders, never read: {}",
                named(&self.dependency_folders.iter().map(|folder| format!("`{folder}`")).collect::<Vec<_>>())
            ));
        }
        if !self.not_read.is_empty() {
            let kinds: Vec<String> = self.not_read.iter().map(|(kind, total)| format!("{total} {kind}")).collect();
            lines.push(format!("files of a kind Krypton does not read: {}", kinds.join(", ")));
        }
        lines
    }

    /// The short note under the score at a terminal, or None when every source file was scored.
    #[must_use]
    pub fn brief(&self) -> Option<String> {
        let left = self.sources_left_out();
        (left > 0).then(|| format!("{} not scored; --full lists them and why", count(left, "source file", "source files")))
    }
}

/// Record one ignored source file under the outermost ignored path that holds it.
fn add_ignored(root: &Path, path: &Path, rules: &Rules, ignored: &mut BTreeMap<String, LeftOut>) {
    let top = outermost_ignored(root, path, &rules.kept);
    let reason = if rules.git_kept.contains(&top) { KRYPTON_IGNORE } else { ".gitignore" };
    let folder = top != path;
    let key = if folder { format!("{}/", relative(root, &top)) } else { relative(root, &top) };
    let entry = ignored.entry(key.clone()).or_insert_with(|| LeftOut { path: key, reason: reason.to_string(), source_files: folder.then_some(0) });
    entry.source_files = entry.source_files.map(|held| held.saturating_add(1));
}

/// The ignored paths as one line per rule.
fn by_rule(ignored: &[LeftOut]) -> Vec<String> {
    [".gitignore", KRYPTON_IGNORE]
        .iter()
        .filter_map(|rule| {
            let paths: Vec<String> = ignored.iter().filter(|left| left.reason == *rule).map(shown_path).collect();
            (!paths.is_empty()).then(|| format!("left out by `{rule}`: {}", named(&paths)))
        })
        .collect()
}

fn shown_path(left: &LeftOut) -> String {
    let path = format!("`{}`", left.path);
    match left.source_files {
        Some(held) => format!("{path} ({})", count(held, "source file", "source files")),
        None => path,
    }
}

/// Up to [`NAMED`] entries, then how many more.
fn named(paths: &[String]) -> String {
    let mut shown: Vec<String> = paths.iter().take(NAMED).cloned().collect();
    if paths.len() > NAMED {
        shown.push(format!("and {} more (`--json` lists every one)", paths.len().saturating_sub(NAMED)));
    }
    shown.join(", ")
}

fn count(total: usize, one: &str, many: &str) -> String {
    format!("{total} {}", if total == 1 { one } else { many })
}

#[cfg(test)]
mod tests {
    use std::fs;

    use serde_json::json;

    use super::*;

    fn tree(name: &str, files: &[(&str, &str)]) -> PathBuf {
        let root = std::env::temp_dir().join(format!("krypton-coverage-{name}-{}", std::process::id()));
        for (path, text) in files {
            let full = root.join(path);
            fs::create_dir_all(full.parent().unwrap()).unwrap();
            fs::write(full, text).unwrap();
        }
        root
    }

    // Each source file left out is named with its rule (whole folders once, with a count), and dependency folders and other kinds of file are listed.
    #[test]
    fn test_coverage_survey() {
        let root = tree(
            "survey",
            &[
                ("app/main.py", "x = 1\n"),
                ("app/style.css", "a{}\n"),
                ("gen/a.py", "x = 1\n"),
                ("gen/b.py", "x = 1\n"),
                ("old.js", "x\n"),
                ("tests/test_a.py", "x = 1\n"),
                ("node_modules/pkg/index.js", "x\n"),
                (".gitignore", "gen/\nold.js\n"),
                (".kryptonignore", "tests/\n"),
            ],
        );
        let coverage = Coverage::survey(&root).unwrap();
        let left = |path: &str, reason: &str, source_files: Option<usize>| LeftOut { path: path.into(), reason: reason.into(), source_files };
        assert_eq!(
            coverage.ignored,
            [left("gen/", ".gitignore", Some(2)), left("old.js", ".gitignore", None), left("tests/", ".kryptonignore", Some(1))],
            "each ignored source, with the rule that left it out"
        );
        assert_eq!(coverage.dependency_folders, ["node_modules/"], "dependency folders, never entered");
        assert_eq!(coverage.not_read.get(".css"), Some(&1), "a stylesheet is a kind Krypton does not read");
        fs::remove_dir_all(&root).unwrap();
    }

    // The server's answer fills in what was scored, set aside and unchanged, and the report names every one.
    #[test]
    fn test_coverage_report() {
        let mut coverage =
            Coverage { ignored: vec![LeftOut { path: "tests/".into(), reason: KRYPTON_IGNORE.into(), source_files: Some(3) }], ..Coverage::default() };
        let answer = json!({
            "files": [{ "filename": "a.py", "parse_error": false }, { "filename": "b.js", "parse_error": true }],
            "skipped_files": [{ "filename": "assets/three.js", "reason": "vendored_name" }],
            "errors": { "E_FILE_TOO_LARGE": "big.py is over the size limit" }
        });
        coverage.add_answer(&answer, 6, 3);
        let text = coverage.markdown();
        assert!(text.contains("Scored 2 source files"), "the count scored: {text}");
        assert!(text.contains("`assets/three.js`: set aside by the server (vendored name)"), "set aside: {text}");
        assert!(text.contains("E_FILE_TOO_LARGE: big.py is over the size limit"), "server errors: {text}");
        assert!(text.contains("`b.js`: did not fully parse"), "parse errors: {text}");
        assert!(text.contains("left out by `.kryptonignore`: `tests/` (3 source files)"), "ignored: {text}");
        assert!(text.contains("3 source files unchanged"), "unchanged: {text}");
        assert_eq!(coverage.brief().as_deref(), Some("7 source files not scored; --full lists them and why"), "the terminal note");
    }

    // A scan that scored everything says so, and the terminal shows no note.
    #[test]
    fn test_coverage_complete() {
        let mut coverage = Coverage::default();
        coverage.add_answer(&json!({ "files": [{ "filename": "a.py" }] }), 1, 1);
        assert!(coverage.markdown().contains("Scored 1 source file (Python, JavaScript and TypeScript). Nothing was left out."), "nothing left out");
        assert_eq!(coverage.brief(), None, "no note");
    }
}
