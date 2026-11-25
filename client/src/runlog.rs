//! The run log: one line per scored run in `.krypton/runs.jsonl`. A run keeps the purity and, for every function at or over the limit, how many
//! times over it is. The real function names are written here, on this machine; the server only ever saw their digests.

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use krypton_client::Names;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::Failure;

/// What one run scored. A function under the limit is absent from `functions`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Run {
    /// 0 is the start of the ticket; each later run counts up. Written as `run` in the log.
    #[serde(rename = "run")]
    pub number: usize,
    pub time: u64,
    pub ticket: String,
    pub purity: i64,
    /// `file::function` to how many times over the limit it is (1.0 is exactly at it).
    pub functions: BTreeMap<String, f64>,
    /// `file::function` to the reason on its `krypton: ignore` comment (`None` when it gives none). A waived function is left out of the
    /// score, so it is kept here to be shown instead. Absent from summaries and logs written before waivers were recorded.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub waived: BTreeMap<String, Option<String>>,
}

fn failure(message: impl Into<String>) -> Failure {
    Failure::Log(message.into())
}

fn write_failure(error: &std::io::Error) -> Failure {
    failure(format!("cannot write the run log: {error}"))
}

/// The folder where the run log and the saved attempts live.
#[must_use]
pub fn folder(repo: &Path) -> PathBuf {
    repo.join(".krypton")
}

/// Create the folder and keep git from ever picking it up: a `.gitignore` of its own ignores everything inside, itself included, so the log and
/// the saved attempts (which hold your code as patches) cannot be committed by accident, even in a repository that forgot to ignore them.
///
/// # Errors
///
/// [`Failure::Log`] when the folder or the file cannot be written.
pub fn ensure_folder(repo: &Path) -> Result<(), Failure> {
    ensure_ignored(&folder(repo)).map_err(|error| write_failure(&error))
}

/// Create `dir` with a `.gitignore` of `*` inside, unless it has one: every folder Krypton writes (`.krypton` in a repository, the login
/// folder) is invisible to git from the moment it exists, whatever the repository's own `.gitignore` says.
///
/// # Errors
///
/// The I/O error when the folder or the marker cannot be written.
pub fn ensure_ignored(dir: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dir)?;
    let marker = dir.join(".gitignore");
    if marker.exists() {
        return Ok(());
    }
    fs::write(marker, "*\n")
}

fn log_path(repo: &Path) -> PathBuf {
    folder(repo).join("runs.jsonl")
}

/// Every run in the log, oldest first (none when there is no log yet).
///
/// # Errors
///
/// [`Failure::Log`] when a line of the log cannot be read: a damaged log is reported, never skipped.
pub fn read_all(repo: &Path) -> Result<Vec<Run>, Failure> {
    let path = log_path(repo);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = fs::read_to_string(&path).map_err(|error| Failure::Read(path.display().to_string(), error))?;
    let lines = text.lines().enumerate().filter(|(_, line)| !line.trim().is_empty());
    lines
        .map(|(index, line)| serde_json::from_str(line).map_err(|error| failure(format!("{} line {}: {error}", path.display(), index.saturating_add(1)))))
        .collect()
}

/// Add one run to the end of the log.
///
/// # Errors
///
/// [`Failure::Log`] when the folder or file cannot be written.
pub fn append(repo: &Path, run: &Run) -> Result<(), Failure> {
    ensure_folder(repo)?;
    let line = serde_json::to_string(run).map_err(|error| failure(error.to_string()))?;
    let mut file = OpenOptions::new().create(true).append(true).open(log_path(repo)).map_err(|error| write_failure(&error))?;
    writeln!(file, "{line}").map_err(|error| write_failure(&error))
}

/// The runs of one ticket, oldest first.
#[must_use]
pub fn for_ticket<'a>(runs: &'a [Run], ticket: &str) -> Vec<&'a Run> {
    runs.iter().filter(|run| run.ticket == ticket).collect()
}

/// The ticket of the newest run.
#[must_use]
pub fn latest_ticket(runs: &[Run]) -> Option<&str> {
    runs.last().map(|run| run.ticket.as_str())
}

fn now() -> Result<u64, Failure> {
    let since = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|error| failure(format!("the clock is before 1970: {error}")))?;
    Ok(since.as_secs())
}

/// A function's name with every dotted part put back: a method arrives as `class_digest.method_digest`, each part hashed on its own.
fn real_name(digest: &str, names: &Names) -> String {
    digest.split('.').map(|part| names.get(part).map_or(part, String::as_str)).collect::<Vec<_>>().join(".")
}

/// A file's functions in the answer, each with its `file::function` key under the real name.
fn functions_of<'a>(file: &'a Value, names: &'a Names) -> impl Iterator<Item = (String, &'a Value)> + 'a {
    let filename = file.get("filename").and_then(Value::as_str);
    let functions = filename.and_then(|_| file.get("functions")).and_then(Value::as_array).into_iter().flatten();
    functions.filter_map(move |function| {
        let digest = function.get("name").and_then(Value::as_str)?;
        Some((format!("{}::{}", filename.unwrap_or_default(), real_name(digest, names)), function))
    })
}

/// `file::function` and how many times over the limit, for every function in the answer that is at or over it.
fn over_the_limit(file: &Value, names: &Names) -> Vec<(String, f64)> {
    functions_of(file, names).filter_map(|(key, function)| Some((key, function.get("times_over_limit").and_then(Value::as_f64)?))).collect()
}

/// The engine's `exempt_reason` as the waiver's own words: `waived: DIGEST` has its reason put back; `waived (no reason given)` has none.
fn waiver_reason(exempt: &str, names: &Names) -> Option<String> {
    let digest = exempt.strip_prefix("waived: ")?;
    Some(names.get(digest).cloned().unwrap_or_else(|| digest.to_string()))
}

/// `file::function` and the reason its waiver gives, if any.
type Waived = (String, Option<String>);

/// Every waived function in the answer, with its reason.
fn waived_in(file: &Value, names: &Names) -> Vec<Waived> {
    functions_of(file, names)
        .filter_map(|(key, function)| {
            let exempt = function.get("exempt_reason").and_then(Value::as_str)?;
            Some((key, waiver_reason(exempt, names)))
        })
        .collect()
}

/// A run built from the server's answer, with the real names put back.
///
/// # Errors
///
/// [`Failure::Log`] when the answer has no purity or no files, or the clock cannot be read.
pub fn from_answer(result: &Value, names: &Names, ticket: &str, run: usize) -> Result<Run, Failure> {
    let purity = result.get("purity_score").and_then(Value::as_i64).ok_or_else(|| failure("the answer has no purity_score"))?;
    let files = result.get("files").and_then(Value::as_array).ok_or_else(|| failure("the answer has no list of files"))?;
    let functions = files.iter().flat_map(|file| over_the_limit(file, names)).collect();
    let waived = files.iter().flat_map(|file| waived_in(file, names)).collect();
    Ok(Run { number: run, time: now()?, ticket: ticket.to_string(), purity, functions, waived })
}

/// Write one run as a standalone JSON file (the summary that `krypton comment` compares).
///
/// # Errors
///
/// [`Failure::Log`] when the file cannot be written.
pub fn write_summary(path: &Path, run: &Run) -> Result<(), Failure> {
    let text = serde_json::to_string_pretty(run).map_err(|error| failure(error.to_string()))?;
    fs::write(path, text).map_err(|error| failure(format!("{}: {error}", path.display())))
}

/// Read a summary written by [`write_summary`].
///
/// # Errors
///
/// [`Failure::Log`] when the file cannot be read or is not a summary.
pub fn read_summary(path: &Path) -> Result<Run, Failure> {
    let text = fs::read_to_string(path).map_err(|error| Failure::Read(path.display().to_string(), error))?;
    serde_json::from_str(&text).map_err(|error| failure(format!("{} is not a Krypton summary: {error}", path.display())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn answer() -> Value {
        json!({ "purity_score": 74, "files": [
            { "filename": "a.py", "functions": [
                { "name": "d1", "times_over_limit": 3.2 }, { "name": "d2", "times_over_limit": null }, { "name": "d3", "times_over_limit": 1.0 } ] },
            { "filename": "b.ts", "functions": [ { "name": "d1", "times_over_limit": 12.0 } ] } ] })
    }

    // Only functions at or over the limit are kept, under their real names.
    #[test]
    fn test_over_limit_only() {
        let names: Names = [("d1".to_string(), "apply".to_string()), ("d3".to_string(), "edge".to_string())].into_iter().collect();
        let run = from_answer(&answer(), &names, "t", 0).unwrap();
        let keys: Vec<&str> = run.functions.keys().map(String::as_str).collect();
        assert_eq!(keys, ["a.py::apply", "a.py::edge", "b.ts::apply"], "the null function is left out and names are real");
        assert_eq!((run.purity, run.number), (74, 0), "purity and run number");
    }

    // A method's class and name are each hashed, and both come back.
    #[test]
    fn test_method_names() {
        let names: Names = [("c1".to_string(), "Client".to_string()), ("m1".to_string(), "connect".to_string())].into_iter().collect();
        let answer = json!({ "purity_score": 60, "files": [ { "filename": "a.py", "functions": [ { "name": "c1.m1", "times_over_limit": 2.0 } ] } ] });
        let run = from_answer(&answer, &names, "t", 0).unwrap();
        assert_eq!(run.functions.keys().next().map(String::as_str), Some("a.py::Client.connect"), "both parts are real names");
    }

    // A waived function is recorded with its reason put back, or with none; it is never counted as over the limit.
    #[test]
    fn test_waived() {
        let names: Names = [("d1".to_string(), "charge".to_string()), ("d2".to_string(), "fmt".to_string()), ("r1".to_string(), "legacy retry".to_string())]
            .into_iter()
            .collect();
        let answer = json!({ "purity_score": 80, "files": [ { "filename": "a.py", "functions": [
            { "name": "d1", "times_over_limit": null, "exempt_reason": "waived: r1" },
            { "name": "d2", "times_over_limit": null, "exempt_reason": "waived (no reason given)" } ] } ] });
        let run = from_answer(&answer, &names, "t", 0).unwrap();
        let expected = BTreeMap::from([("a.py::charge".to_string(), Some("legacy retry".to_string())), ("a.py::fmt".to_string(), None)]);
        assert_eq!(run.waived, expected, "reasons put back");
        assert!(run.functions.is_empty(), "waived functions are not over the limit");
    }

    // A summary written before waivers were recorded still reads, and one without waivers does not write the field.
    #[test]
    fn test_waived_optional() {
        let run: Run = serde_json::from_str(r#"{"run":0,"time":0,"ticket":"t","purity":70,"functions":{}}"#).unwrap();
        assert!(run.waived.is_empty(), "old summary reads");
        assert!(!serde_json::to_string(&run).unwrap().contains("waived"), "empty list not written");
    }

    // A folder Krypton writes is invisible to git, even in a repository whose own .gitignore says nothing about it.
    #[test]
    fn test_ensure_ignored() {
        let repo = std::env::temp_dir().join(format!("krypton-ignored-{}", std::process::id()));
        std::fs::create_dir_all(&repo).unwrap();
        let git = |args: &[&str]| std::process::Command::new("git").arg("-C").arg(&repo).args(args).output().unwrap();
        git(&["init", "-q"]);
        let dir = repo.join(".krypton");
        ensure_ignored(&dir).unwrap();
        std::fs::write(dir.join("credentials"), "secret").unwrap();
        assert!(git(&["check-ignore", "-q", ".krypton/credentials"]).status.success(), "git ignores what is inside");
        assert!(!String::from_utf8_lossy(&git(&["status", "--porcelain", "--untracked-files=all"]).stdout).contains(".krypton"), "git status does not list it");
        std::fs::remove_dir_all(&repo).unwrap();
    }

    // An answer without a purity score is an error, not a zero.
    #[test]
    fn test_missing_purity() {
        let error = from_answer(&json!({ "files": [] }), &Names::new(), "t", 0).unwrap_err();
        assert!(matches!(error, Failure::Log(_)), "a log failure");
    }
}
