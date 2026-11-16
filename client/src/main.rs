//! `krypton`: the Krypton client. It parses code on this machine and sends only the syntax tree to a Krypton server.
//!
//! * `krypton scan REPO` builds the tree of a folder, sends it, prints the review.
//! * `krypton order REPO` asks the server whether a different file order would keep more of a prompt cache.
//! * `krypton wire FILE...` prints the request body without sending it.
//!
//! Every failure is reported with its reason and a non-zero exit code; there is no fallback.

use std::env;
use std::process::ExitCode;

use krypton_client::{WalkError, WireError};

mod diff;
mod http;
mod order_command;
mod scan_command;
mod tokens;
mod wire_command;

const USAGE: &str = "usage: krypton scan REPO | order REPO | wire FILE...   (the server address is --server or KRYPTON_SERVER)";

/// Why the command stopped: bad usage (exit 2), or a file, wire or server problem (exit 1).
#[derive(Debug)]
enum Failure {
    Usage(String),
    Read(String, std::io::Error),
    Walk(WalkError),
    Wire(WireError),
    Output(String),
    Server(String),
    /// The server answered and the review says the code is flagged: the answer is already printed.
    Flagged,
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
    match run() {
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
        Err(Failure::Server(message)) => {
            eprintln!("{message}");
            ExitCode::from(1)
        }
        Err(Failure::Flagged) => ExitCode::from(1),
    }
}

fn run() -> Result<(), Failure> {
    let args: Vec<String> = env::args().skip(1).collect();
    let rest = args.get(1..).unwrap_or_default();
    match args.first().map(String::as_str) {
        Some("scan") => scan_command::run(rest),
        Some("order") => order_command::run(rest),
        Some("wire") => wire_command::run(rest),
        _ => Err(Failure::Usage(USAGE.to_string())),
    }
}
