//! `krypton`: the Krypton client. It parses code on this machine and sends only the syntax tree to a Krypton server.
//!
//! * `krypton scan REPO` builds the tree of a folder, sends it, prints the review.
//! * `krypton order REPO` asks the server whether a different file order would keep more of a prompt cache.
//! * `krypton wire FILE...` prints the request body without sending it.
//! * `krypton scan --log` keeps a run log and an attempt per run; `krypton review` says which functions are stuck; `krypton restore` puts a run back.
//! * `krypton comment` writes the pull request comment from two scan summaries; `krypton report` has the Krypton server post it as the
//!   Krypton GitHub App (exit 3 when the app is not set up or installed, so the Action posts it itself).
//! * `krypton login` / `krypton logout`: the server scores only for a logged-in user (GitHub's device flow) or a GitHub Actions run.
//! * `krypton impact` shows what a change reaches downstream: the functions it touched and every function that calls them, from a call graph
//!   built on this machine (see `callgraph/`). Nothing is sent and nothing is scored.
//! * `krypton prepush` is the check before a git push: the hook `krypton setup` installs hands it the commits being pushed, and it stops a
//!   push that breaks the pull request gate's rule (see `prepush_command.rs` and `hook.rs`).
//! * `krypton setup` asks once whether stuck functions go to a person (a local review page) or are approved automatically; `krypton` alone, in a
//!   terminal, starts it.
//! * After `scan` and `setup` at a terminal, at most once a day: a newer release is offered (see `update.rs`); `krypton update` updates.
//!
//! Every failure is reported with its reason and a non-zero exit code; there is no fallback.

use std::env;
use std::process::ExitCode;

use krypton_client::{WalkError, WireError};

mod attempts;
mod callgraph;
mod cancel;
mod comment_command;
mod coverage;
mod diff;
mod hook;
mod http;
mod impact_command;
mod loader;
mod login_command;
mod name_key;
mod order_command;
mod prepush_command;
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
    "usage: krypton setup | login | logout | scan REPO | impact [REPO] | order REPO | wire FILE... | review | restore --run N | comment BEFORE AFTER | report | update";

/// Why the command stopped. Each kind has its own exit code (see [`exit`]) and, after the message, a hint on how to fix it.
#[derive(Debug)]
enum Failure {
    /// Wrong flags or arguments (exit 2).
    Usage(String),
    /// A file could not be read (exit 4; exit 5 when this computer refused: out of memory or no permission).
    Read(String, std::io::Error),
    /// A source file could not be turned into a tree (exit 4).
    Walk(WalkError),
    /// The request could not be built from the trees (exit 4).
    Wire(WireError),
    /// Output could not be written (exit 5).
    Output(String),
    /// The run log, the saved attempts or git could not do what was asked (exit 5).
    Log(String),
    /// The server could not be reached, even after trying again (exit 6).
    Unreachable(String),
    /// The server is busy or rate limited, even after waiting as it asked (exit 7).
    Busy(String),
    /// The server refused the request: a login, a size or a version it does not accept (exit 8).
    Refused(String),
    /// The server failed while scoring, or answered with something that cannot be read (exit 9).
    Server(String),
    /// Setup could not finish: the local settings page could not start or stopped early (exit 10).
    Setup(String),
    /// The person pressed Ctrl+C or Esc during a wait (exit 130).
    Cancelled,
    /// The server answered and the review says the code is flagged (exit 1): the answer is already printed.
    Flagged,
    /// `krypton report` was not posted because the Krypton app is not set up or not installed (exit 3): the Action posts it itself.
    NotPosted(String),
}

/// The exit codes, one per cause, so a script or the GitHub Action can tell a code problem (1) from Krypton's own (6, 7, 9).
mod exit_code {
    pub const FLAGGED: u8 = 1;
    pub const USAGE: u8 = 2;
    pub const NOT_POSTED: u8 = 3;
    pub const TREE: u8 = 4;
    pub const SYSTEM: u8 = 5;
    pub const UNREACHABLE: u8 = 6;
    pub const BUSY: u8 = 7;
    pub const REFUSED: u8 = 8;
    pub const SERVER: u8 = 9;
    pub const SETUP: u8 = 10;
}

const HINT_TREE: &str = "Check that the file is readable and is valid Python, JavaScript or TypeScript. To skip it, add it to .kryptonignore.";
const HINT_SYSTEM: &str =
    "This is this computer, not your code: free some memory or disk space, or check that you may write to this folder, then run it again.";
const HINT_UNREACHABLE: &str = "Check your internet connection (and KRYPTON_SERVER, if you set it). If the connection is fine, the Krypton server may be down: try again in a few minutes.";
const HINT_BUSY: &str = "The Krypton server is busy, or this login sent many requests in a minute. Wait a minute and run it again.";
const HINT_REFUSED: &str = "The message above says why. If it asks you to log in, run krypton login; if the project is too large, skip folders with .kryptonignore; if the client is too old, run krypton update.";
const HINT_SERVER: &str =
    "This is on Krypton's side, not your code. Try again in a few minutes, and report it at https://github.com/pinklamb/Krypton/issues if it keeps happening.";
const HINT_SETUP: &str = "Another program may be using port 7777: close it, or set KRYPTON_LISTEN to another address. Or answer in the terminal instead: krypton setup, without --page.";

/// Whether this computer itself refused (memory, permission), as opposed to the file being missing or unreadable.
fn computer_refused(error: &std::io::Error) -> bool {
    matches!(error.kind(), std::io::ErrorKind::OutOfMemory | std::io::ErrorKind::PermissionDenied)
}

/// How a failure ends the command: its exit code, what to print (nothing when the answer is already printed), and the hint after it.
#[derive(Debug, PartialEq, Eq)]
struct Exit {
    code: u8,
    message: Option<String>,
    hint: Option<&'static str>,
}

const fn ends(code: u8, message: Option<String>, hint: Option<&'static str>) -> Exit {
    Exit { code, message, hint }
}

/// The exit code, message and hint for a failure.
fn exit(failure: Failure) -> Exit {
    match failure {
        Failure::Flagged => ends(exit_code::FLAGGED, None, None),
        Failure::Usage(message) => ends(exit_code::USAGE, Some(message), None),
        Failure::NotPosted(message) => ends(exit_code::NOT_POSTED, Some(message), None),
        Failure::Read(path, error) if computer_refused(&error) => ends(exit_code::SYSTEM, Some(format!("cannot read {path}: {error}")), Some(HINT_SYSTEM)),
        Failure::Read(path, error) => ends(exit_code::TREE, Some(format!("cannot read {path}: {error}")), Some(HINT_TREE)),
        Failure::Walk(error) => ends(exit_code::TREE, Some(error.to_string()), Some(HINT_TREE)),
        Failure::Wire(error) => ends(exit_code::TREE, Some(error.to_string()), Some(HINT_TREE)),
        Failure::Output(message) => ends(exit_code::SYSTEM, Some(format!("cannot write the output: {message}")), Some(HINT_SYSTEM)),
        Failure::Log(message) => ends(exit_code::SYSTEM, Some(message), Some(HINT_SYSTEM)),
        Failure::Unreachable(message) => ends(exit_code::UNREACHABLE, Some(message), Some(HINT_UNREACHABLE)),
        Failure::Busy(message) => ends(exit_code::BUSY, Some(message), Some(HINT_BUSY)),
        Failure::Refused(message) => ends(exit_code::REFUSED, Some(message), Some(HINT_REFUSED)),
        Failure::Server(message) => ends(exit_code::SERVER, Some(message), Some(HINT_SERVER)),
        Failure::Setup(message) => ends(exit_code::SETUP, Some(message), Some(HINT_SETUP)),
        Failure::Cancelled => ends(cancel::CANCELLED, Some("Cancelled.".to_string()), None),
    }
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

/// Print why the command stopped, then how to fix it, and exit with the failure's own code.
fn report(done: Result<(), Failure>) -> ExitCode {
    let Err(failure) = done else { return ExitCode::SUCCESS };
    let Exit { code, message, hint } = exit(failure);
    if let Some(message) = message {
        eprintln!("{message}");
    }
    if let Some(hint) = hint {
        eprintln!("Hint: {hint} (exit {code})");
    }
    ExitCode::from(code)
}

fn run(args: &[String]) -> Result<(), Failure> {
    let rest = args.get(1..).unwrap_or_default();
    match args.first().map(String::as_str) {
        Some("setup") => setup_command::run(rest),
        Some("login") => login_command::run(rest),
        Some("logout") => login_command::logout(rest),
        None if setup_command::interactive() => setup_command::run(&[]),
        Some("scan") => scan_command::run(rest),
        Some("impact") => impact_command::run(rest),
        Some("order") => order_command::run(rest),
        Some("wire") => wire_command::run(rest),
        Some("review") => review_command::run(rest),
        Some("restore") => restore_command::run(rest),
        Some("comment") => comment_command::comment(rest),
        Some("prepush") => prepush_command::run(rest),
        Some("report") => report_command::run(rest),
        _ => Err(Failure::Usage(USAGE.to_string())),
    }
}

#[cfg(test)]
mod tests {
    use std::io::{Error, ErrorKind};

    use super::*;

    fn code(failure: Failure) -> u8 {
        exit(failure).code
    }

    // Each cause has its own exit code, so a script or the Action can tell the code's problem (1) from Krypton's own.
    #[test]
    fn test_exit_codes() {
        let said = || "x".to_string();
        let codes = [
            code(Failure::Flagged),
            code(Failure::Usage(said())),
            code(Failure::NotPosted(said())),
            code(Failure::Read(said(), Error::from(ErrorKind::NotFound))),
            code(Failure::Read(said(), Error::from(ErrorKind::OutOfMemory))),
            code(Failure::Unreachable(said())),
            code(Failure::Busy(said())),
            code(Failure::Refused(said())),
            code(Failure::Server(said())),
            code(Failure::Setup(said())),
            code(Failure::Cancelled),
        ];
        assert_eq!(codes, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 130], "one code per cause");
    }

    // Every failure but a flagged review and bad usage comes with a hint on how to fix it.
    #[test]
    fn test_hints() {
        assert!(exit(Failure::Busy("busy".to_string())).hint.is_some_and(|hint| hint.contains("Wait a minute")), "busy says wait");
        assert!(exit(Failure::Setup("port".to_string())).hint.is_some_and(|hint| hint.contains("7777")), "setup names the port");
        assert_eq!(exit(Failure::Flagged), ends(1, None, None), "a flagged review has already printed its answer");
    }
}
