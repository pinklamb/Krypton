//! `krypton scan REPO [--level relaxed|standard|strict] [--base REF] [--names] [--json] [--save FILE] [--log] [--ticket TEXT] [--summary FILE] [--no-loader]`: build the tree of every source file under
//! REPO on this machine, send only the tree to the Krypton server and print its review. The server never receives source text; names, strings and
//! comments stay here, and `--names` puts the real function names back into the review locally. `--log` also adds the run to `.krypton/runs.jsonl` and
//! saves an attempt (a patch) so the run can be put back; `--summary FILE` writes the scores with real names for `krypton comment`.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use krypton_client::{Builder, ClientConfig, Names};
use serde_json::Value;

use crate::attempts;
use crate::diff::changed_since;
use crate::http::Server;
use crate::loader::{self, Loader};
use crate::runlog;
use crate::Failure;

const USAGE: &str =
    "usage: krypton scan REPO [--level relaxed|standard|strict] [--base REF] [--names] [--json] [--save FILE] [--log] [--ticket TEXT] [--summary FILE] [--no-loader]";
const EXTENSIONS: [&str; 9] = ["py", "js", "jsx", "mjs", "cjs", "ts", "mts", "cts", "tsx"];
const SKIP_DIRS: [&str; 12] =
    [".git", "node_modules", "dist", "build", "venv", ".venv", "__pycache__", "target", ".next", "coverage", "vendor", "site-packages"];
const LEVELS: [&str; 3] = ["relaxed", "standard", "strict"];

/// How the answer is shown.
struct Output {
    names: bool,
    json: bool,
}

struct Options {
    repo: PathBuf,
    level: String,
    server: Option<String>,
    output: Output,
    no_loader: bool,
    base: Option<String>,
    save: Option<PathBuf>,
    log: bool,
    ticket: String,
    summary: Option<PathBuf>,
}

fn usage(message: &str) -> Failure {
    Failure::Usage(format!("{message}\n{USAGE}"))
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let mut options = Options {
        repo: PathBuf::new(),
        level: "standard".to_string(),
        server: None,
        output: Output { names: false, json: false },
        no_loader: false,
        base: None,
        save: None,
        log: false,
        ticket: String::new(),
        summary: None,
    };
    let mut repo = None;
    let mut words = args.iter();
    while let Some(word) = words.next() {
        let mut value = |flag: &str| words.next().cloned().ok_or_else(|| usage(&format!("{flag} needs a value")));
        match word.as_str() {
            "--names" => options.output.names = true,
            "--json" => options.output.json = true,
            "--no-loader" => options.no_loader = true,
            "--log" => options.log = true,
            "--ticket" => options.ticket = value("--ticket")?,
            "--summary" => options.summary = Some(PathBuf::from(value("--summary")?)),
            "--base" => options.base = Some(value("--base")?),
            "--server" => options.server = Some(value("--server")?),
            "--save" => options.save = Some(PathBuf::from(value("--save")?)),
            "--level" => {
                options.level = value("--level")?;
                if !LEVELS.contains(&options.level.as_str()) {
                    return Err(usage("--level must be one of relaxed, standard, strict"));
                }
            }
            flag if flag.starts_with("--") => return Err(usage(&format!("unknown option {flag}"))),
            path => repo = Some(PathBuf::from(path)),
        }
    }
    options.repo = repo.ok_or_else(|| usage("give the repository folder"))?;
    Ok(options)
}

fn is_source(path: &Path) -> bool {
    path.extension().and_then(|extension| extension.to_str()).is_some_and(|extension| EXTENSIONS.contains(&extension.to_ascii_lowercase().as_str()))
}

/// The file, next to `.gitignore`, that lists more paths for Krypton to leave out, in the same syntax.
pub const KRYPTON_IGNORE: &str = ".kryptonignore";

/// The source files under `root` as forward-slash paths relative to it, sorted. Left out: whatever the repository's `.gitignore` files
/// and `.git/info/exclude` ignore (git's own rules, nested files and `!` included, even outside a git checkout), whatever `.kryptonignore`
/// files add in the same syntax, and the usual build and dependency folders even when nothing ignores them. Hidden files are scanned
/// unless something ignores them; the user's global git excludes are not read, so a scan gives the same answer on every machine.
fn source_files(root: &Path) -> Result<Vec<String>, Failure> {
    let walker = ignore::WalkBuilder::new(root)
        .hidden(false)
        .git_global(false)
        .require_git(false)
        .add_custom_ignore_filename(KRYPTON_IGNORE)
        .filter_entry(|entry| !(entry.file_type().is_some_and(|kind| kind.is_dir()) && SKIP_DIRS.contains(&entry.file_name().to_string_lossy().as_ref())))
        .build();
    let mut found = BTreeSet::new();
    for entry in walker {
        let entry = entry.map_err(|error| Failure::Read(root.display().to_string(), std::io::Error::other(error.to_string())))?;
        let path = entry.path();
        if entry.file_type().is_some_and(|kind| kind.is_file()) && is_source(path) {
            found.extend(path.strip_prefix(root).ok().map(|relative| relative.to_string_lossy().replace('\\', "/")));
        }
    }
    Ok(found.into_iter().collect())
}

/// Put the real names back where the server wrote their digests.
fn put_names_back(text: &str, names: &Names) -> Result<String, Failure> {
    let table: Value = serde_json::to_value(names).map_err(|error| Failure::Output(error.to_string()))?;
    let restored = table
        .as_object()
        .into_iter()
        .flatten()
        .fold(text.to_string(), |text, (digest, name)| text.replace(digest.as_str(), name.as_str().unwrap_or(digest.as_str())));
    Ok(restored)
}

/// The request body, and the real names that stay on this machine.
type Built = (Vec<u8>, Names);

/// The tree of every file to score, as the request body, and the real names that stay here. With `--base`, only the files changed since that ref,
/// each with the lines that changed.
fn build_body(options: &Options, files: &[String], loader: Option<&Loader>) -> Result<Built, Failure> {
    let altered = options.base.as_deref().map(|base| changed_since(base, &options.repo, files)).transpose()?;
    let selected: Vec<&String> = files.iter().filter(|name| altered.as_ref().is_none_or(|changed| changed.contains_key(name.as_str()))).collect();
    if selected.is_empty() {
        return Err(Failure::Usage("no source files changed since that ref: nothing to score".to_string()));
    }
    note(loader, &format!("building the tree of {} files ...", selected.len()));
    let count = selected.len();
    let builder = Builder::new(ClientConfig::from_overrides(Value::Null)?, crate::name_key::for_this_run()?)?;
    let known: BTreeSet<String> = selected.iter().map(|name| (*name).clone()).collect();
    let (mut wire_files, mut names) = (Vec::new(), Names::new());
    for (index, name) in selected.into_iter().enumerate() {
        progress(loader, index, count);
        let path = options.repo.join(name);
        let bytes = fs::read(&path).map_err(|error| Failure::Read(path.display().to_string(), error))?;
        let mut wire = builder.file(name, &bytes, &known, &mut names)?;
        wire.altered_lines = altered.as_ref().and_then(|changed| changed.get(name.as_str())).map(|lines| lines.iter().copied().collect());
        wire_files.push(wire);
    }
    progress(loader, count, count);
    let body = serde_json::to_vec(&builder.request(wire_files, &options.level, "summary")).map_err(|error| Failure::Output(error.to_string()))?;
    Ok((body, names))
}

/// A progress line on standard error, left out when the loader is drawing there.
fn note(loader: Option<&Loader>, text: &str) {
    if loader.is_none() {
        eprintln!("{text}");
    }
}

/// Tell the loader how many files are done.
fn progress(loader: Option<&Loader>, done: usize, total: usize) {
    if let Some(active) = loader {
        active.update(done, total);
    }
}

pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let files = source_files(&options.repo)?;
    if files.is_empty() {
        return Err(Failure::Usage(format!("no source files found under {}", options.repo.display())));
    }
    let loader = loader::start_if_wanted(options.no_loader);
    let (body, names) = build_body(&options, &files, loader.as_ref())?;
    note(loader.as_ref(), &format!("sending {} KB of tree (no source text) ...", body.len() / 1000));
    let answer = Server::from_flag(options.server.as_deref()).post("/v1/score", &body)?;
    if let Some(active) = loader {
        active.finish();
    }
    if let Some(path) = &options.save {
        fs::write(path, serde_json::to_string_pretty(&answer).map_err(|error| Failure::Output(error.to_string()))?)
            .map_err(|error| Failure::Output(format!("{}: {error}", path.display())))?;
    }
    let result = answer.get("result").cloned().unwrap_or(Value::Null);
    record(&options, &result, &names)?;
    let conclusion = result.get("check_conclusion").and_then(Value::as_str).unwrap_or("unknown");
    if options.output.json {
        println!("{}", serde_json::to_string_pretty(&result).map_err(|error| Failure::Output(error.to_string()))?);
    } else {
        print_review(&result, answer.get("usage").unwrap_or(&Value::Null), options.output.names.then_some(&names))?;
    }
    if conclusion == "success" || options.summary.is_some() {
        Ok(())
    } else {
        Err(Failure::Flagged)
    }
}

/// With `--log` or `--summary`: the scores of this run under their real names, added to the run log (with the attempt) and/or written to a file.
fn record(options: &Options, result: &Value, names: &Names) -> Result<(), Failure> {
    if !options.log && options.summary.is_none() {
        return Ok(());
    }
    let number = runlog::for_ticket(&runlog::read_all(&options.repo)?, &options.ticket).len();
    let run = runlog::from_answer(result, names, &options.ticket, number)?;
    if let Some(path) = &options.summary {
        runlog::write_summary(path, &run)?;
    }
    if options.log {
        attempts::log_run(&options.repo, &run)?;
        eprintln!("logged run {number} of ticket {:?} and saved its attempt", options.ticket);
    }
    Ok(())
}

/// The review as text, with the real names put back when asked, and a one-line total.
fn print_review(result: &Value, usage: &Value, names: Option<&Names>) -> Result<(), Failure> {
    let summary = result.get("summary_markdown").and_then(Value::as_str).unwrap_or_default();
    println!("{}", if let Some(names) = names { put_names_back(summary, names)? } else { summary.to_string() });
    let number = |value: Option<&Value>| value.map_or_else(|| "?".to_string(), ToString::to_string);
    println!(
        "purity {}/100 ({}) | {} files, {} nodes, scored in {} ms",
        number(result.get("purity_score")),
        result.get("check_conclusion").and_then(Value::as_str).unwrap_or("unknown"),
        number(usage.get("files")),
        number(usage.get("nodes")),
        number(usage.get("ms"))
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // The scan leaves out what .gitignore ignores (no git checkout needed), what .kryptonignore adds, and dependency folders.
    #[test]
    fn test_ignores() {
        let root = std::env::temp_dir().join(format!("krypton-ignore-{}", std::process::id()));
        let file = |name: &str, text: &str| {
            let path = root.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text).unwrap();
        };
        file("app/main.py", "x = 1\n");
        file("app/gen/schema_pb2.py", "x = 1\n");
        file("tests/fixtures/big.py", "x = 1\n");
        file("tests/fixtures/keep.py", "x = 1\n");
        file("node_modules/pkg/index.js", "x\n");
        file(".gitignore", "gen/\n");
        file(".kryptonignore", "tests/fixtures/*.py\n!tests/fixtures/keep.py\n");
        let found = source_files(&root).unwrap();
        assert_eq!(found, ["app/main.py", "tests/fixtures/keep.py"], ".gitignore, .kryptonignore with a ! exception, and node_modules");
        fs::remove_dir_all(&root).unwrap();
    }
}
