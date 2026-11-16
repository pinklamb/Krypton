//! `krypton wire [--root DIR] [--level L] [--detail D] [--config FILE.json] FILE...`: print the POST /v1/score body for the files.
//!
//! `krypton wire --stdin-json` reads `{"path": "source text", ...}` from standard input instead, so a program that holds a repository in
//! memory (the demo) never has to write one to disk. A `.gitattributes` entry, when present, adds its `linguist-vendored` and
//! `linguist-generated` patterns to the server's defaults.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{self, BufWriter, Read, Write};
use std::path::{Path, PathBuf};

use krypton_client::{linguist_globs, Builder, ClientConfig, Names};
use serde_json::{json, Value};

use crate::Failure;

const LEVELS: [&str; 3] = ["relaxed", "standard", "strict"];
const DETAILS: [&str; 2] = ["summary", "full"];
const USAGE: &str = "usage: krypton wire [--root DIR] [--level relaxed|standard|strict] [--detail summary|full] [--config FILE.json] [--files-from LIST.txt] [--names-out NAMES.json] FILE...  |  krypton wire --stdin-json [same options]";

struct Options {
    root: Option<PathBuf>,
    level: String,
    detail: String,
    config: Option<PathBuf>,
    files: Vec<PathBuf>,
    names_out: Option<PathBuf>,
    stdin_json: bool,
}

fn usage(message: &str) -> Failure {
    Failure::Usage(format!("{message}\n{USAGE}"))
}

/// The value after a flag, or a usage error naming the flag.
fn value_of(flag: &str, words: &mut std::slice::Iter<String>) -> Result<String, Failure> {
    words.next().cloned().ok_or_else(|| usage(&format!("{flag} needs a value")))
}

/// One of the allowed words, or a usage error.
fn choice(flag: &str, value: String, allowed: &[&str]) -> Result<String, Failure> {
    if allowed.contains(&value.as_str()) {
        Ok(value)
    } else {
        Err(usage(&format!("{flag} must be one of {}", allowed.join(", "))))
    }
}

fn parse_args(args: &[String]) -> Result<Options, Failure> {
    let mut options = Options {
        root: None,
        level: "standard".to_string(),
        detail: "summary".to_string(),
        config: None,
        files: Vec::new(),
        names_out: None,
        stdin_json: false,
    };
    let mut words = args.iter();
    while let Some(word) = words.next() {
        match word.as_str() {
            "--root" => options.root = Some(PathBuf::from(value_of("--root", &mut words)?)),
            "--stdin-json" => options.stdin_json = true,
            "--config" => options.config = Some(PathBuf::from(value_of("--config", &mut words)?)),
            "--names-out" => options.names_out = Some(PathBuf::from(value_of("--names-out", &mut words)?)),
            "--files-from" => options.files.extend(read_list(Path::new(&value_of("--files-from", &mut words)?))?),
            "--level" => options.level = choice("--level", value_of("--level", &mut words)?, &LEVELS)?,
            "--detail" => options.detail = choice("--detail", value_of("--detail", &mut words)?, &DETAILS)?,
            flag if flag.starts_with("--") => return Err(usage(&format!("unknown option {flag}"))),
            file => options.files.push(PathBuf::from(file)),
        }
    }
    if options.files.is_empty() && !options.stdin_json {
        return Err(usage("give at least one file"));
    }
    Ok(options)
}

/// The overrides in the config file, or none.
fn read_config(path: Option<&Path>) -> Result<Value, Failure> {
    let Some(path) = path else { return Ok(Value::Null) };
    let text = fs::read_to_string(path).map_err(|error| Failure::Read(path.display().to_string(), error))?;
    serde_json::from_str(&text).map_err(|error| Failure::Usage(format!("{} is not JSON: {error}", path.display())))
}

/// The files named in a list, one path per line (blank lines skipped).
fn read_list(path: &Path) -> Result<Vec<PathBuf>, Failure> {
    let text = fs::read_to_string(path).map_err(|error| Failure::Read(path.display().to_string(), error))?;
    Ok(text.lines().map(str::trim).filter(|line| !line.is_empty()).map(PathBuf::from).collect())
}

/// The path the server sees: relative to --root when given, with forward slashes.
fn wire_path(file: &Path, root: Option<&Path>) -> Result<String, Failure> {
    let relative = match root {
        Some(root) => file.strip_prefix(root).map_err(|error| usage(&format!("{} is not under --root ({error})", file.display())))?,
        None => file,
    };
    Ok(relative.to_string_lossy().replace('\\', "/"))
}

/// Every path of the request as the server will see it; imports are resolved against this set.
fn known_paths(options: &Options) -> Result<BTreeSet<String>, Failure> {
    options.files.iter().map(|file| wire_path(file, options.root.as_deref())).collect()
}

/// The names stay on the client: written to a file only when asked, so a later step can put them back into the server's answer.
fn write_names(path: &Path, names: &Names) -> Result<(), Failure> {
    let text = serde_json::to_string(names).map_err(|error| Failure::Output(error.to_string()))?;
    fs::write(path, text).map_err(|error| Failure::Output(format!("{}: {error}", path.display())))
}

/// The repository on standard input as `{path: text}`, sorted by path.
fn read_repository() -> Result<BTreeMap<String, String>, Failure> {
    let mut bytes = Vec::new();
    io::stdin().read_to_end(&mut bytes).map_err(|error| Failure::Read("standard input".to_string(), error))?;
    serde_json::from_slice(&bytes).map_err(|error| Failure::Usage(format!("standard input must be a JSON object of path -> text: {error}")))
}

/// The config with the repository's own vendored and generated patterns added (only when it declares some). They go as `*_extra`: the server
/// adds them to its own defaults, so the client never needs to know the defaults.
fn with_attributes(mut config: Value, attributes: &str) -> Value {
    let (vendored, generated) = linguist_globs(attributes);
    let Some(map) = config.as_object_mut() else { return config };
    for (key, extra) in [("vendored_path_globs_extra", vendored), ("generated_path_globs_extra", generated)] {
        if !extra.is_empty() && !map.contains_key(key) {
            map.insert(key.to_string(), json!(extra));
        }
    }
    config
}

fn finish(builder: &Builder, files: Vec<krypton_client::WireFile>, names: &Names, options: &Options) -> Result<(), Failure> {
    if let Some(path) = &options.names_out {
        write_names(path, names)?;
    }
    let request = builder.request(files, &options.level, &options.detail);
    let mut out = BufWriter::new(io::stdout().lock());
    serde_json::to_writer(&mut out, &request).map_err(|error| Failure::Output(error.to_string()))?;
    out.flush().map_err(|error| Failure::Output(error.to_string()))
}

fn run_stdin(options: &Options) -> Result<(), Failure> {
    let mut repository = read_repository()?;
    let attributes = repository.remove(".gitattributes").unwrap_or_default();
    let config = with_attributes(
        match read_config(options.config.as_deref())? {
            Value::Null => json!({}),
            other => other,
        },
        &attributes,
    );
    let builder = Builder::new(ClientConfig::from_overrides(config)?)?;
    let known: BTreeSet<String> = repository.keys().cloned().collect();
    let (mut files, mut names) = (Vec::new(), Names::new());
    for (path, text) in &repository {
        files.push(builder.file(path, text.as_bytes(), &known, &mut names)?);
    }
    finish(&builder, files, &names, options)
}

pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_args(args)?;
    if options.stdin_json {
        return run_stdin(&options);
    }
    let builder = Builder::new(ClientConfig::from_overrides(read_config(options.config.as_deref())?)?)?;
    let known = known_paths(&options)?;
    let (mut files, mut names) = (Vec::new(), Names::new());
    for file in &options.files {
        let bytes = fs::read(file).map_err(|error| Failure::Read(file.display().to_string(), error))?;
        files.push(builder.file(&wire_path(file, options.root.as_deref())?, &bytes, &known, &mut names)?);
    }
    finish(&builder, files, &names, &options)
}
