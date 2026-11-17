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
    let dir = folder(repo);
    fs::create_dir_all(&dir).map_err(|error| write_failure(&error))?;
    let marker = dir.join(".gitignore");
    if marker.exists() {
        return Ok(());
    }
    fs::write(
        &marker, "*
",
    )
    .map_err(|error| write_failure(&error))
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

/// `file::function` and how many times over the limit, for every function in the answer that is at or over it.
fn over_the_limit(file: &Value, names: &Names) -> Vec<(String, f64)> {
    let Some(filename) = file.get("filename").and_then(Value::as_str) else { return Vec::new() };
    let functions = file.get("functions").and_then(Value::as_array).into_iter().flatten();
    functions
        .filter_map(|function| {
            let times = function.get("times_over_limit").and_then(Value::as_f64)?;
            let digest = function.get("name").and_then(Value::as_str)?;
            Some((format!("{filename}::{}", names.get(digest).map_or(digest, String::as_str)), times))
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
    Ok(Run { number: run, time: now()?, ticket: ticket.to_string(), purity, functions })
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

    // An answer without a purity score is an error, not a zero.
    #[test]
    fn test_missing_purity() {
        let error = from_answer(&json!({ "files": [] }), &Names::new(), "t", 0).unwrap_err();
        assert!(matches!(error, Failure::Log(_)), "a log failure");
    }
}
