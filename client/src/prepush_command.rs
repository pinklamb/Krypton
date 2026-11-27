//! `krypton prepush [REMOTE [URL]]`: the check before a git push. Git runs the hook that `krypton setup` installs (see `hook.rs`) before
//! every push and hands it one line per ref on standard input: `<local ref> <local sha> <remote ref> <remote sha>`. For each branch being
//! pushed, Krypton scores the code at the commit the remote has now and at the commit being pushed, and applies the pull request gate's
//! rule: never worse (the default), or, when setup's gate is the target, the files the push changes must reach the target.
//!
//! A push that breaks the rule exits 1, and git stops it (`git push --no-verify` skips the check once). A push Krypton cannot score (no
//! login, the server down, a commit this clone does not have) goes ahead with a warning, so the check never traps anyone. Tags, deleted
//! branches and a new branch with no remote default to compare against are not checked, and the line says so.

use std::fs;
use std::io::{self, BufRead};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use crate::comment_command::{gate_line, waiver_notes, NewCode};
use crate::runlog::{self, Run};
use crate::setup_command;
use crate::workflow::Gate;
use crate::Failure;

/// One ref git is about to push.
#[derive(Debug, PartialEq, Eq)]
struct Pushed {
    local_ref: String,
    local: String,
    remote: String,
}

/// One line of git's pre-push input, or `None` when it is not one.
fn parse_line(line: &str) -> Option<Pushed> {
    let mut words = line.split_whitespace();
    let (local_ref, local, _remote_ref, remote) = (words.next()?, words.next()?, words.next()?, words.next()?);
    Some(Pushed { local_ref: local_ref.to_string(), local: local.to_string(), remote: remote.to_string() })
}

/// Git's "no commit" (a branch being created or deleted), in SHA-1 or SHA-256.
fn is_zero(sha: &str) -> bool {
    !sha.is_empty() && sha.chars().all(|digit| digit == '0')
}

/// What the gate holds a push to: never worse, or the changed files reaching the target.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Rule {
    NeverWorse,
    Target(i64),
}

impl Rule {
    /// The rule setup chose for the pull request gate; never worse when setup has not chosen the target.
    fn from_settings(repo: &Path) -> Result<Self, Failure> {
        let settings = setup_command::saved(repo)?;
        Ok(match settings {
            Some(chosen) if chosen.gate == Gate::Target => Self::Target(chosen.target),
            _ => Self::NeverWorse,
        })
    }

    fn words(self) -> String {
        match self {
            Self::NeverWorse => "it makes the code worse (the never-worse gate)".to_string(),
            Self::Target(target) => format!("the code it changes scores below your target of {target}"),
        }
    }
}

/// What the check said about one ref.
#[derive(Debug, PartialEq, Eq)]
enum Outcome {
    /// The gate line, and the waivers the push adds, one line each.
    Passed(String, Vec<String>),
    Failed(String, Vec<String>),
    Skipped(String),
}

fn git(repo: &Path, args: &[&str]) -> Option<String> {
    let done = Command::new("git").arg("-C").arg(repo).args(args).output().ok()?;
    done.status.success().then(|| String::from_utf8_lossy(&done.stdout).trim().to_string())
}

/// The commit to compare against: what the remote has now, or, for a new branch, where it left the remote's default branch.
fn base_for(repo: &Path, pushed: &Pushed, remote_name: &str) -> Result<String, String> {
    if !is_zero(&pushed.remote) {
        return git(repo, &["cat-file", "-e", &format!("{}^{{commit}}", pushed.remote)])
            .map(|_| pushed.remote.clone())
            .ok_or_else(|| "the remote has commits this clone has not fetched (git fetch, then push again)".to_string());
    }
    let default = git(repo, &["rev-parse", "--verify", "--quiet", &format!("refs/remotes/{remote_name}/HEAD")])
        .ok_or_else(|| format!("a new branch, and {remote_name} has no default branch here to compare with (git remote set-head {remote_name} --auto)"))?;
    git(repo, &["merge-base", &pushed.local, &default]).ok_or_else(|| "a new branch with no history in common with the default branch".to_string())
}

/// The files of `sha` written into `dir`, as git stores them (no working-tree changes, nothing written into `.git`).
fn export(repo: &Path, sha: &str, dir: &Path) -> Result<(), Failure> {
    fs::create_dir_all(dir).map_err(|error| Failure::Log(format!("cannot create {}: {error}", dir.display())))?;
    let mut archive = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["archive", "--format=tar", sha])
        .stdout(Stdio::piped())
        .spawn()
        .map_err(|error| Failure::Log(format!("cannot run git archive: {error}")))?;
    let tar = archive.stdout.take().ok_or_else(|| Failure::Log("git archive gave no output".to_string()))?;
    let unpacked = Command::new("tar").arg("-x").arg("-C").arg(dir).stdin(tar).status().map_err(|error| Failure::Log(format!("cannot run tar: {error}")))?;
    let archived = archive.wait().map_err(|error| Failure::Log(format!("git archive did not finish: {error}")))?;
    if archived.success() && unpacked.success() {
        Ok(())
    } else {
        Err(Failure::Log(format!("could not unpack {sha} into {}", dir.display())))
    }
}

/// Score a folder quietly and read back its summary.
fn score(folder: &Path, summary: &Path) -> Result<Run, Failure> {
    let args = [folder.display().to_string(), "--summary".to_string(), summary.display().to_string(), "--quiet".to_string()];
    crate::scan_command::run(&args)?;
    runlog::read_summary(summary)
}

/// The target rule's verdict on the lines this push changed, scored in the working tree against the base.
fn changed_code(repo: &Path, base: &str, target: i64, summary: &Path) -> Result<NewCode, Failure> {
    let args =
        [repo.display().to_string(), "--base".to_string(), base.to_string(), "--summary".to_string(), summary.display().to_string(), "--quiet".to_string()];
    match crate::scan_command::run(&args) {
        Ok(()) => Ok(NewCode::Scored { purity: runlog::read_summary(summary)?.purity, target }),
        Err(Failure::Usage(message)) if message.contains("nothing to score") => Ok(NewCode::NoSourceChanged { target }),
        Err(other) => Err(other),
    }
}

/// Score one ref before and after the push and apply the rule.
fn check(repo: &Path, pushed: &Pushed, base: &str, rule: Rule, work: &Path) -> Result<Outcome, Failure> {
    let (before_dir, after_dir) = (work.join("before"), work.join("after"));
    export(repo, base, &before_dir)?;
    export(repo, &pushed.local, &after_dir)?;
    let before = score(&before_dir, &work.join("before.json"))?;
    let after = score(&after_dir, &work.join("after.json"))?;
    let new_code = match rule {
        Rule::NeverWorse => NewCode::Unchecked,
        Rule::Target(target) => changed_code(repo, base, target, &work.join("changed.json"))?,
    };
    let line = gate_line(&before, &after, new_code);
    let notes = waiver_notes(&before, &after);
    let failed = (rule == Rule::NeverWorse && after.purity < before.purity) || new_code.fails();
    Ok(if failed { Outcome::Failed(line, notes) } else { Outcome::Passed(line, notes) })
}

/// Check one line of git's input: skipped for tags, deletions and pushes with no base; scored otherwise. A push Krypton cannot score is
/// skipped with the reason, never stopped.
fn check_line(repo: &Path, pushed: &Pushed, remote_name: &str, rule: Rule, work: &Path) -> Outcome {
    if !pushed.local_ref.starts_with("refs/heads/") {
        return Outcome::Skipped("not a branch".to_string());
    }
    if is_zero(&pushed.local) {
        return Outcome::Skipped("a branch being deleted".to_string());
    }
    let base = match base_for(repo, pushed, remote_name) {
        Ok(base) => base,
        Err(why) => return Outcome::Skipped(why),
    };
    if base == pushed.local {
        return Outcome::Skipped("nothing new to push".to_string());
    }
    let outcome = check(repo, pushed, &base, rule, work).unwrap_or_else(|error| Outcome::Skipped(format!("could not be scored: {error:?}")));
    if let Err(error) = fs::remove_dir_all(work) {
        eprintln!("krypton: could not clear {}: {error}", work.display());
    }
    outcome
}

/// Say what happened to one ref, on standard error where git shows a hook's output; true when it stops the push.
fn tell(branch: &str, outcome: &Outcome, rule: Rule) -> bool {
    match outcome {
        Outcome::Passed(line, notes) => {
            eprintln!("krypton: {branch} · {line}");
            for note in notes {
                eprintln!("krypton: {branch} · {note}");
            }
            false
        }
        Outcome::Failed(line, notes) => {
            eprintln!("krypton: {branch} · {line}");
            for note in notes {
                eprintln!("krypton: {branch} · {note}");
            }
            eprintln!("krypton: push stopped: {}. Fix it, or push anyway once with: git push --no-verify", rule.words());
            true
        }
        Outcome::Skipped(why) => {
            eprintln!("krypton: {branch} not checked ({why}); it goes ahead.");
            false
        }
    }
}

/// A fresh folder for one ref's two checkouts.
fn work_folder(index: usize) -> PathBuf {
    std::env::temp_dir().join(format!("krypton-prepush-{}-{index}", std::process::id()))
}

pub fn run(args: &[String]) -> Result<(), Failure> {
    let repo = PathBuf::from(".");
    let remote_name = args.first().map_or("origin", String::as_str);
    let rule = Rule::from_settings(&repo)?;
    let lines: Vec<String> = io::stdin().lock().lines().collect::<Result<_, _>>().map_err(|error| Failure::Read("standard input".to_string(), error))?;
    let mut stopped = false;
    for (index, pushed) in lines.iter().filter_map(|line| parse_line(line)).enumerate() {
        let branch = pushed.local_ref.trim_start_matches("refs/heads/");
        let outcome = check_line(&repo, &pushed, remote_name, rule, &work_folder(index));
        stopped |= tell(branch, &outcome, rule);
    }
    if stopped {
        return Err(Failure::Flagged);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // Git's input lines are read word by word, and a short line is not one.
    #[test]
    fn test_parse_line() {
        let line = "refs/heads/main 1111111111111111111111111111111111111111 refs/heads/main 0000000000000000000000000000000000000000";
        let pushed = parse_line(line).unwrap();
        assert_eq!((pushed.local_ref.as_str(), is_zero(&pushed.local), is_zero(&pushed.remote)), ("refs/heads/main", false, true), "ref, new branch");
        assert!(parse_line("refs/heads/main abc").is_none(), "too short");
        assert!(!is_zero(""), "empty is not a zero commit");
    }

    // Tags and deleted branches are not checked, and never stop a push.
    #[test]
    fn test_skips() {
        let work = std::env::temp_dir().join("krypton-prepush-test-skip");
        let zero = "0".repeat(40);
        let tag = Pushed { local_ref: "refs/tags/v1".into(), local: "1".repeat(40), remote: zero.clone() };
        let deleted = Pushed { local_ref: "refs/heads/old".into(), local: zero, remote: "1".repeat(40) };
        assert_eq!(check_line(Path::new("."), &tag, "origin", Rule::NeverWorse, &work), Outcome::Skipped("not a branch".into()), "tag");
        assert_eq!(check_line(Path::new("."), &deleted, "origin", Rule::NeverWorse, &work), Outcome::Skipped("a branch being deleted".into()), "deleted");
        assert!(!tell("old", &Outcome::Skipped("x".into()), Rule::NeverWorse), "a skip never stops the push");
        assert!(tell("main", &Outcome::Failed("Purity 71% → 70% (−1)".into(), Vec::new()), Rule::Target(70)), "a failure stops it");
        let waived = Outcome::Passed("Purity ▲ 70% → 74% (+4) · 1 new waiver".into(), vec!["waived a.py · charge (no reason given)".into()]);
        assert!(!tell("main", &waived, Rule::NeverWorse), "a waiver is shown, never stops the push");
    }
}
