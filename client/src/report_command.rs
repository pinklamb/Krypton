//! `krypton report --repo OWNER/NAME --pr N --sha SHA --state success|failure|error --line FILE --scorecard FILE --details URL` hands a pull
//! request's report to the Krypton server, which posts it as the Krypton GitHub App: the one comment, edited in place, and the gate line, so
//! both show Krypton's name and crystal instead of "github-actions". The Action runs it after `krypton login --github-actions`; the server
//! lets a run report only on its own repository.
//!
//! Exit code 3 means it was not posted because the app is not set up on that server or not installed on the repository; the Action then
//! posts the same comment and line as github-actions instead.

use std::fs;
use std::path::PathBuf;

use serde_json::json;

use crate::http::Server;
use crate::Failure;

const USAGE: &str = "usage: krypton report --repo OWNER/NAME --pr N --sha SHA --state success|failure|error --line FILE --scorecard FILE --details URL";

#[derive(Default)]
struct Options {
    repo: String,
    pr: u64,
    sha: String,
    state: String,
    line: PathBuf,
    scorecard: PathBuf,
    details: String,
}

fn usage(message: &str) -> Failure {
    Failure::Usage(format!("{message}\n{USAGE}"))
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let mut options = Options::default();
    let mut words = args.iter();
    while let Some(word) = words.next() {
        let mut value = |flag: &str| words.next().cloned().ok_or_else(|| usage(&format!("{flag} needs a value")));
        match word.as_str() {
            "--repo" => options.repo = value("--repo")?,
            "--pr" => options.pr = value("--pr")?.parse().map_err(|_bad| usage("--pr is the pull request's number"))?,
            "--sha" => options.sha = value("--sha")?,
            "--state" => options.state = value("--state")?,
            "--line" => options.line = PathBuf::from(value("--line")?),
            "--scorecard" => options.scorecard = PathBuf::from(value("--scorecard")?),
            "--details" => options.details = value("--details")?,
            other => return Err(usage(&format!("unknown option {other}"))),
        }
    }
    if options.repo.is_empty() || options.pr == 0 || options.sha.is_empty() || options.details.is_empty() || options.line.as_os_str().is_empty() {
        return Err(usage("--repo, --pr, --sha, --line, --scorecard and --details are all needed"));
    }
    if !matches!(options.state.as_str(), "success" | "failure" | "error") {
        return Err(usage("--state is success, failure or error (Krypton could not score the push)"));
    }
    Ok(options)
}

fn read(path: &PathBuf) -> Result<String, Failure> {
    fs::read_to_string(path).map_err(|error| Failure::Read(path.display().to_string(), error))
}

/// Whether a refusal means "post it yourself": the app is not set up on the server, or not installed on the repository.
#[must_use]
pub fn means_fall_back(message: &str) -> bool {
    message.contains("app_not_installed") || message.contains("app_not_configured")
}

/// # Errors
///
/// [`Failure::NotPosted`] when the Action should post it itself, [`Failure::Server`] for any other refusal, and usage or read errors.
pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let body = json!({
        "repo": options.repo,
        "pr": options.pr,
        "sha": options.sha,
        "state": options.state,
        "description": read(&options.line)?.trim(),
        "target_url": options.details,
        "body": read(&options.scorecard)?,
    });
    let bytes = serde_json::to_vec(&body).map_err(|error| Failure::Output(error.to_string()))?;
    match Server::from_flag(None)?.post("/v1/pr/report", &bytes) {
        Ok(answer) => {
            let comment = answer.get("comment").and_then(serde_json::Value::as_str).unwrap_or("posted");
            println!("Posted as the Krypton app: comment {comment}, gate line set.");
            Ok(())
        }
        Err(Failure::Server(message) | Failure::Refused(message)) if means_fall_back(&message) => Err(Failure::NotPosted(message)),
        Err(other) => Err(other),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(text: &str) -> Vec<String> {
        text.split_whitespace().map(ToString::to_string).collect()
    }

    // Every field is needed, and the state is one of three words.
    #[test]
    fn test_report_options() {
        let full = "--repo a/b --pr 12 --sha bbfce90 --state success --line l.txt --scorecard s.md --details https://x";
        assert!(parse_options(&args(full)).is_ok_and(|options| options.pr == 12 && options.repo == "a/b"), "all given");
        assert!(parse_options(&args("--repo a/b --pr 12")).is_err(), "missing fields");
        assert!(parse_options(&args(&full.replace("success", "maybe"))).is_err(), "a bad state");
        assert!(parse_options(&args(&full.replace("success", "error"))).is_ok(), "error: Krypton could not score the push");
        assert!(parse_options(&args(&full.replace("--pr 12", "--pr x"))).is_err(), "a bad number");
    }

    // Only "not set up" and "not installed" mean the Action posts it itself.
    #[test]
    fn test_falls_back() {
        assert!(means_fall_back(r#"the server refused the request (404): {"code":"app_not_installed"}"#), "not installed");
        assert!(means_fall_back(r#"the server refused the request (501): {"code":"app_not_configured"}"#), "not set up");
        assert!(!means_fall_back(r#"the server refused the request (403): {"code":"not_your_repository"}"#), "a real refusal is an error");
    }
}
