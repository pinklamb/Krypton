//! `krypton`: the Krypton client. It parses code on this machine and sends only the syntax tree to a Krypton server.
//!
//! * `krypton scan REPO` builds the tree of a folder, sends it, prints the review.
//! * `krypton order REPO` asks the server whether a different file order would keep more of a prompt cache.
//! * `krypton wire FILE...` prints the request body without sending it.
//! * `krypton scan --log` keeps a run log and an attempt per run; `krypton review` says which functions are stuck; `krypton restore` puts a run back.
//! * `krypton comment` writes the pull request comment from two scan summaries; `krypton report` has the Krypton server post it as the
//!   Krypton GitHub App (exit 3 when the app is not set up or installed, so the Action posts it itself).
//! * `krypton login` / `krypton logout`: the server scores only for a logged-in user (GitHub's device flow) or a GitHub Actions run.
//! * `krypton setup` asks once whether stuck functions go to a person (a local review page) or are approved automatically; `krypton` alone, in a
//!   terminal, starts it.
//! * After `scan` and `setup` at a terminal, at most once a day: a newer release is offered (see `update.rs`); `krypton update` updates.
//!
//! Every failure is reported with its reason and a non-zero exit code; there is no fallback.

use std::env;
use std::process::ExitCode;

use krypton_client::{WalkError, WireError};

mod attempts;
mod comment_command;
mod diff;
mod http;
mod loader;
mod login_command;
mod name_key;
mod order_command;
mod report;
mod report_command;
mod restore_command;
mod review_command;
mod runlog;
mod scan_command;
mod setup_command;
mod setup_page;
mod tokens;
mod update;
mod wire_command;
mod workflow;

const USAGE: &str =
    "usage: krypton setup | login | logout | scan REPO | order REPO | wire FILE... | review | restore --run N | comment BEFORE AFTER | report | update";

/// Why the command stopped: bad usage (exit 2), or a file, wire or server problem (exit 1).
#[derive(Debug)]
enum Failure {
    Usage(String),
    Read(String, std::io::Error),
    Walk(WalkError),
    Wire(WireError),
    Output(String),
    Server(String),
    /// The run log, the saved attempts or git could not do what was asked.
    Log(String),
    /// The server answered and the review says the code is flagged: the answer is already printed.
    Flagged,
    /// `krypton report` was not posted because the Krypton app is not set up or not installed (exit 3): the Action posts it itself.
    NotPosted(String),
}

impl From<WireError> for Failure {
    fn from(error: WireError) -> Self {
        Self::Wire(error)
    }
}

impl From<WalkError> for Failure {
    fn from(error: WalkError) -> Self {
        Self::Walk(error)
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("update") {
        return ExitCode::from(update::run());
    }
    let done = run(&args);
    // a person at a terminal, after a scan or setup that ran: offer a newer release, at most once a day
    let offered = matches!(args.first().map(String::as_str), None | Some("scan" | "setup")) && matches!(done, Ok(()) | Err(Failure::Flagged));
    if offered && update::offer() {
        return ExitCode::from(update::UPDATE_EXIT);
    }
    report(done)
}

fn report(done: Result<(), Failure>) -> ExitCode {
    match done {
        Ok(()) => ExitCode::SUCCESS,
        Err(Failure::Usage(message)) => {
            eprintln!("{message}");
            ExitCode::from(2)
        }
        Err(Failure::Read(path, error)) => {
            eprintln!("cannot read {path}: {error}");
            ExitCode::from(1)
        }
        Err(Failure::Walk(error)) => {
            eprintln!("{error}");
            ExitCode::from(1)
        }
        Err(Failure::Wire(error)) => {
            eprintln!("{error}");
            ExitCode::from(1)
        }
        Err(Failure::Output(message)) => {
            eprintln!("cannot write the request: {message}");
            ExitCode::from(1)
        }
        Err(Failure::Server(message) | Failure::Log(message)) => {
            eprintln!("{message}");
            ExitCode::from(1)
        }
        Err(Failure::Flagged) => ExitCode::from(1),
        Err(Failure::NotPosted(message)) => {
            eprintln!("{message}");
            ExitCode::from(3)
        }
    }
}

fn run(args: &[String]) -> Result<(), Failure> {
    let rest = args.get(1..).unwrap_or_default();
    match args.first().map(String::as_str) {
        Some("setup") => setup_command::run(rest),
        Some("login") => login_command::run(rest),
        Some("logout") => login_command::logout(rest),
        None if setup_command::interactive() => setup_command::run(&[]),
        Some("scan") => scan_command::run(rest),
        Some("order") => order_command::run(rest),
        Some("wire") => wire_command::run(rest),
        Some("review") => review_command::run(rest),
        Some("restore") => restore_command::run(rest),
        Some("comment") => comment_command::comment(rest),
        Some("report") => report_command::run(rest),
        _ => Err(Failure::Usage(USAGE.to_string())),
    }
}
