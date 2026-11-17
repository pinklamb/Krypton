//! `krypton restore --run N [REPO] [--ticket TEXT] [--dry-run] [--force]`: put the working tree back to what it was at run N of a ticket, from the
//! attempt saved when that run was logged. `--dry-run` only checks that it can be done and lists the files.

use std::path::PathBuf;

use crate::attempts::{restore, Request};
use crate::runlog;
use crate::Failure;

const USAGE: &str = "usage: krypton restore --run N [REPO] [--ticket TEXT] [--dry-run] [--force]";

struct Options {
    repo: PathBuf,
    ticket: Option<String>,
    run: Option<usize>,
    dry_run: bool,
    force: bool,
}

fn usage(message: &str) -> Failure {
    Failure::Usage(format!("{message}\n{USAGE}"))
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let mut options = Options { repo: PathBuf::from("."), ticket: None, run: None, dry_run: false, force: false };
    let mut words = args.iter();
    while let Some(word) = words.next() {
        let mut value = |flag: &str| words.next().cloned().ok_or_else(|| usage(&format!("{flag} needs a value")));
        match word.as_str() {
            "--dry-run" => options.dry_run = true,
            "--force" => options.force = true,
            "--ticket" => options.ticket = Some(value("--ticket")?),
            "--run" => options.run = Some(value("--run")?.parse().map_err(|_not_a_number| usage("--run needs a whole number"))?),
            flag if flag.starts_with("--") => return Err(usage(&format!("unknown option {flag}"))),
            path => options.repo = PathBuf::from(path),
        }
    }
    Ok(options)
}

pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let target = options.run.ok_or_else(|| usage("say which run to put back with --run N"))?;
    let all = runlog::read_all(&options.repo)?;
    let ticket =
        options.ticket.clone().or_else(|| runlog::latest_ticket(&all).map(str::to_string)).ok_or_else(|| Failure::Log("no runs are logged yet".to_string()))?;
    let mine = runlog::for_ticket(&all, &ticket);
    let request = Request { repo: &options.repo, ticket: &ticket, runs: &mine, target, dry_run: options.dry_run, force: options.force };
    let outcome = restore(&request)?;
    if outcome.already {
        println!("already at run {target}: nothing to change");
    } else {
        let verb = if options.dry_run { "would put back" } else { "put back" };
        println!("{verb} run {target} ({} files): {}", outcome.files.len(), outcome.files.join(", "));
    }
    Ok(())
}
