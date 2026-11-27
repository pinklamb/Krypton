//! `krypton order REPO [--subdir DIR] [--ext .py,.ts] [--files LIST|-] [--tokens-from COUNTS.json] [--post] [--json]`
//! reads the repository's git history on this machine and builds the `POST /v1/order` body: the prompt's files with token counts, and which of
//! them each recent commit touched. `--model` picks the tokenizer the counts come from (see `tokens.rs`). File names and numbers only: no source text, commit messages or authors leave the machine. With
//! `--post` it sends the body and prints the server's decision; without, it prints the body.

use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fs;
use std::io::{self, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::{json, Value};

use crate::http::Server;
use crate::tokens::Counter;

use crate::Failure;

fn read_input(path: &str) -> Result<Vec<u8>, Failure> {
    if path == "-" {
        let mut bytes = Vec::new();
        io::stdin().read_to_end(&mut bytes).map_err(|error| Failure::Read("standard input".to_string(), error))?;
        return Ok(bytes);
    }
    fs::read(path).map_err(|error| Failure::Read(path.to_string(), error))
}

/// The newest this many commits are read: the decision looks at the latest 200 and replays against the 200 before them.
const COMMITS: &str = "-n400";
const USAGE: &str =
    "usage: krypton order REPO [--subdir DIR] [--ext .py,.ts] [--files LIST.txt|-] [--model NAME | --tokenizer o200k_base|cl100k_base|estimate | --tokens-from COUNTS.json] [--post] [--json]";

struct Options {
    repo: PathBuf,
    subdir: String,
    suffixes: Vec<String>,
    files: Option<String>,
    tokens_from: Option<PathBuf>,
    model: Option<String>,
    tokenizer: Option<String>,
    server: Option<String>,
    post: bool,
    json: bool,
}

fn usage(message: &str) -> Failure {
    Failure::Usage(format!("{message}\n{USAGE}"))
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let mut options = Options {
        repo: PathBuf::new(),
        subdir: ".".to_string(),
        suffixes: vec![".py".to_string()],
        files: None,
        tokens_from: None,
        model: None,
        tokenizer: None,
        server: None,
        post: false,
        json: false,
    };
    let mut words = args.iter();
    let mut repo = None;
    while let Some(word) = words.next() {
        let mut value = |flag: &str| words.next().cloned().ok_or_else(|| usage(&format!("{flag} needs a value")));
        match word.as_str() {
            "--subdir" => options.subdir = value("--subdir")?,
            "--ext" => options.suffixes = value("--ext")?.split(',').map(|part| part.trim().to_string()).collect(),
            "--files" => options.files = Some(value("--files")?),
            "--server" => {
                options.server = Some(value("--server")?);
                options.post = true;
            }
            "--post" => options.post = true,
            "--json" => options.json = true,
            "--model" => options.model = Some(value("--model")?),
            "--tokenizer" => options.tokenizer = Some(value("--tokenizer")?),
            "--tokens-from" => options.tokens_from = Some(PathBuf::from(value("--tokens-from")?)),
            flag if flag.starts_with("--") => return Err(usage(&format!("unknown option {flag}"))),
            path => repo = Some(PathBuf::from(path)),
        }
    }
    options.repo = repo.ok_or_else(|| usage("give the repository"))?;
    Ok(options)
}

/// Lines printed by a git command run in `folder`; `None` when git cannot answer (no repository, no commits yet).
fn git_lines(folder: &Path, args: &[&str]) -> Option<Vec<String>> {
    let done = Command::new("git").arg("-C").arg(folder).args(["-c", "core.quotepath=off"]).args(args).output().ok()?;
    done.status.success().then(|| String::from_utf8_lossy(&done.stdout).lines().map(str::to_string).collect())
}

fn needs_git(folder: &Path, args: &[&str]) -> Result<Vec<String>, Failure> {
    git_lines(folder, args).ok_or_else(|| Failure::Usage(format!("cannot read git history in {}: is it a git repository with commits?", folder.display())))
}

fn wanted(name: &str, suffixes: &[String]) -> bool {
    suffixes.iter().any(|suffix| name.ends_with(suffix.as_str()))
}

/// The files of the request: `--files` (one per line, the caller's order) or every matching tracked and untracked file, sorted.
fn request_files(options: &Options) -> Result<Vec<String>, Failure> {
    if let Some(list) = &options.files {
        let text = if list == "-" {
            String::from_utf8_lossy(&read_input("-")?).into_owned()
        } else {
            fs::read_to_string(list).map_err(|error| Failure::Read(list.clone(), error))?
        };
        let names: Vec<String> = text.lines().map(str::trim).filter(|line| !line.is_empty()).map(str::to_string).collect();
        if names.iter().collect::<HashSet<_>>().len() != names.len() {
            return Err(Failure::Usage("--files lists a file twice".to_string()));
        }
        return Ok(names);
    }
    let prefix = if options.subdir == "." || options.subdir.is_empty() { String::new() } else { format!("{}/", options.subdir.trim_end_matches('/')) };
    let listed = needs_git(
        &options.repo,
        &["ls-files", "--cached", "--others", "--exclude-standard", "--", if prefix.is_empty() { "." } else { options.subdir.as_str() }],
    )?;
    let names: BTreeSet<String> = listed
        .iter()
        .filter(|name| wanted(name, &options.suffixes) && options.repo.join(name).is_file())
        .filter_map(|name| name.strip_prefix(prefix.as_str()).map(str::to_string))
        .collect();
    Ok(names.into_iter().collect())
}

/// The files each recent commit touched under the folder, oldest commit first; only names in `files` are kept (a commit that touched none stays as an empty entry, because it still counts toward the history's length).
fn read_history(base: &Path, files: &HashSet<&str>) -> Result<Vec<Vec<String>>, Failure> {
    let lines = needs_git(base, &["log", "--first-parent", COMMITS, "--name-only", "--pretty=format:@@", "--relative", "--", "."])?;
    let mut commits: Vec<Vec<String>> = Vec::new();
    for line in lines {
        if line == "@@" {
            commits.push(Vec::new());
        } else if let (true, Some(current)) = (files.contains(line.as_str()), commits.last_mut()) {
            if !current.contains(&line) {
                current.push(line);
            }
        }
    }
    commits.reverse();
    Ok(commits)
}

/// Paths modified, staged or untracked right now (relative to the folder).
fn uncommitted(base: &Path) -> BTreeSet<String> {
    let changed = git_lines(base, &["diff", "--name-only", "--relative", "HEAD", "--", "."]).unwrap_or_default();
    let untracked = git_lines(base, &["ls-files", "--others", "--exclude-standard"]).unwrap_or_default();
    changed.into_iter().chain(untracked).filter(|line| !line.is_empty()).collect()
}

/// Token counts from a JSON file of `{path: count}`, or from the tokenizer of the model the prompt is for.
fn token_counts(options: &Options, base: &Path, names: &[String]) -> Result<BTreeMap<String, u64>, Failure> {
    if let Some(path) = &options.tokens_from {
        let text = fs::read_to_string(path).map_err(|error| Failure::Read(path.display().to_string(), error))?;
        let counts: BTreeMap<String, u64> =
            serde_json::from_str(&text).map_err(|error| Failure::Usage(format!("{} must be a JSON object of path -> token count: {error}", path.display())))?;
        if let Some(missing) = names.iter().find(|name| !counts.contains_key(name.as_str())) {
            return Err(Failure::Usage(format!("{} has no count for {missing}", path.display())));
        }
        return Ok(counts);
    }
    let mut counter = Counter::choose(options.tokenizer.as_deref(), options.model.as_deref(), &options.repo)?;
    eprintln!("tokens: {}", counter.describe());
    let all = names
        .iter()
        .map(|name| {
            let bytes = fs::read(base.join(name)).map_err(|error| Failure::Read(base.join(name).display().to_string(), error))?;
            Ok((name.clone(), counter.count(&String::from_utf8_lossy(&bytes))?))
        })
        .collect();
    counter.save();
    all
}

pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let base = options.repo.join(&options.subdir);
    let names = request_files(&options)?;
    if names.is_empty() {
        return Err(Failure::Usage(format!("no files with suffix {} found under {}", options.suffixes.join(","), options.subdir)));
    }
    if let Some(missing) = names.iter().find(|name| !base.join(name).is_file()) {
        return Err(Failure::Usage(format!("not found under {}: {missing}", options.subdir)));
    }
    let known: HashSet<&str> = names.iter().map(String::as_str).collect();
    let history = read_history(&base, &known)?;
    let in_flux: Vec<String> = uncommitted(&base).into_iter().filter(|name| known.contains(name.as_str())).collect();
    let counts = token_counts(&options, &base, &names)?;
    let files: Vec<Value> = names.iter().map(|name| json!({ "path": name, "tokens": counts.get(name).copied().unwrap_or(0) })).collect();
    let body = json!({ "wire_version": 1, "files": files, "history": history, "in_flux": in_flux });
    if options.post {
        let answer =
            Server::from_flag(options.server.as_deref())?.post("/v1/order", &serde_json::to_vec(&body).map_err(|error| Failure::Output(error.to_string()))?)?;
        let result = answer.get("result").cloned().unwrap_or(Value::Null);
        let text = if options.json {
            serde_json::to_string_pretty(&result).map_err(|error| Failure::Output(error.to_string()))?
        } else {
            render(&result, names.len())
        };
        println!("{text}");
        return Ok(());
    }
    let mut out = BufWriter::new(io::stdout().lock());
    serde_json::to_writer(&mut out, &body).map_err(|error| Failure::Output(error.to_string()))?;
    out.flush().map_err(|error| Failure::Output(error.to_string()))
}

/// The orders the server tried, best first: `name 63.0%, name 61.2%, ...`.
fn tried_line(result: &Value) -> String {
    let mut tried: Vec<(String, f64)> = result
        .get("tried")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| (row.get("strategy").and_then(Value::as_str).unwrap_or_default().to_string(), row.get("kept").and_then(Value::as_f64).unwrap_or(0.0)))
        .collect();
    tried.sort_by(|a, b| b.1.total_cmp(&a.1));
    tried.iter().map(|(name, kept)| format!("{name} {:.1}%", kept * 100.0)).collect::<Vec<_>>().join(", ")
}

/// The human summary of the server's decision: what it decided and why, every order it tried, then the order when it changed.
fn render(result: &Value, files: usize) -> String {
    let text = |key: &str| result.get(key).and_then(Value::as_str).unwrap_or_default().to_string();
    let backtest = result.get("backtest").cloned().unwrap_or(Value::Null);
    let share = |key: &str| backtest.get(key).and_then(Value::as_f64).unwrap_or(0.0) * 100.0;
    let mut lines = vec![
        format!("{}: {}", text("action").to_uppercase(), text("reason")),
        format!(
            "  {files} files; backtest over {} past changes: yours kept {:.1}% of the prompt, the best order ({}) {:.1}%",
            backtest.get("changes").and_then(Value::as_u64).unwrap_or(0),
            share("baseline_kept"),
            text("strategy"),
            share("krypton_kept")
        ),
        format!("  orders tried: {}", tried_line(result)),
    ];
    if text("action") == "reorder" {
        lines.push("  send the files in this order:".to_string());
        lines.extend(result.get("order").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).map(|name| format!("    {name}")));
    } else {
        lines.push("  send the request unchanged.".to_string());
    }
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::tried_line;

    // The orders tried are listed best first.
    #[test]
    fn test_tried_line() {
        let result = json!({ "tried": [{ "strategy": "alphabetical", "kept": 0.47 }, { "strategy": "stable-first", "kept": 0.71 }] });
        assert_eq!(tried_line(&result), "stable-first 71.0%, alphabetical 47.0%");
    }
}
