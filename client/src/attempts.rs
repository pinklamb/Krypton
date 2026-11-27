//! Attempts: each logged run saves the state of the working tree as a patch against `HEAD` (changed files, new files, deletions) in
//! `.krypton/attempts/`, so any run can be put back later. No commits and no branches are made, and nothing outside the patches is touched.

use std::collections::BTreeSet;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use crate::runlog::{self, Run};
use crate::Failure;

fn failure(message: impl Into<String>) -> Failure {
    Failure::Log(message.into())
}

/// Run git in `repo` and return what it printed, when its exit code is one of `accepted`.
fn git(repo: &Path, args: &[&str], accepted: &[i32]) -> Result<Vec<u8>, Failure> {
    let done = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["-c", "core.quotepath=off"])
        .args(args)
        .output()
        .map_err(|error| failure(format!("cannot run git: {error}")))?;
    if accepted.contains(&done.status.code().unwrap_or(-1)) {
        return Ok(done.stdout);
    }
    Err(failure(format!("git {} failed: {}", args.join(" "), String::from_utf8_lossy(&done.stderr).trim())))
}

/// Run git in `repo` with `input` on its standard input.
fn git_input(repo: &Path, args: &[&str], input: &str) -> Result<(), Failure> {
    let mut child = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| failure(format!("cannot run git: {error}")))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(input.as_bytes()).map_err(|error| failure(format!("cannot give git the patch: {error}")))?;
    }
    let done = child.wait_with_output().map_err(|error| failure(format!("git did not finish: {error}")))?;
    if done.status.success() {
        return Ok(());
    }
    Err(failure(format!("git {} failed: {}", args.join(" "), String::from_utf8_lossy(&done.stderr).trim())))
}

/// Patches are made from the top folder of the repository, where the paths in them start.
fn require_top(repo: &Path) -> Result<(), Failure> {
    let prefix = git(repo, &["rev-parse", "--show-prefix"], &[0])?;
    if String::from_utf8_lossy(&prefix).trim().is_empty() {
        return Ok(());
    }
    Err(failure("run this from the top folder of the git repository: attempts are saved as patches against it"))
}

fn untracked(repo: &Path) -> Result<Vec<String>, Failure> {
    let listed = git(repo, &["ls-files", "--others", "--exclude-standard", "-z"], &[0])?;
    let names = listed.split(|byte| *byte == 0).filter(|name| !name.is_empty()).map(|name| String::from_utf8_lossy(name).into_owned());
    Ok(names.filter(|name| !name.starts_with(".krypton/")).collect())
}

fn new_file_patch(repo: &Path, name: &str) -> Result<String, Failure> {
    let args = ["diff", "--no-index", "--binary", "--no-color", "--src-prefix=a/", "--dst-prefix=b/", "--", "/dev/null", name];
    Ok(String::from_utf8_lossy(&git(repo, &args, &[0, 1])?).into_owned())
}

/// The working tree as a patch against `HEAD`: edits and deletions of tracked files, plus every new file that is not ignored.
///
/// # Errors
///
/// [`Failure::Log`] when this is not the top folder of a git repository with a first commit, or git fails.
pub fn snapshot(repo: &Path) -> Result<String, Failure> {
    require_top(repo)?;
    let args = ["diff", "HEAD", "--binary", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", "--", ".", ":(exclude).krypton"];
    let mut patch = String::from_utf8_lossy(&git(repo, &args, &[0])?).into_owned();
    for name in untracked(repo)? {
        patch.push_str(&new_file_patch(repo, &name)?);
    }
    Ok(patch)
}

fn slug(ticket: &str) -> String {
    let text: String = ticket.chars().map(|letter| if letter.is_ascii_alphanumeric() { letter.to_ascii_lowercase() } else { '-' }).take(40).collect();
    if text.trim_matches('-').is_empty() {
        "ticket".to_string()
    } else {
        text
    }
}

/// Where the patch of one run of a ticket is kept.
#[must_use]
pub fn patch_path(repo: &Path, ticket: &str, run: usize) -> PathBuf {
    runlog::folder(repo).join("attempts").join(format!("{}-run{run}.patch", slug(ticket)))
}

fn save(path: &Path, text: &str) -> Result<(), Failure> {
    let folder = path.parent().ok_or_else(|| failure("the attempts folder has no parent"))?;
    fs::create_dir_all(folder).map_err(|error| failure(format!("cannot create {}: {error}", folder.display())))?;
    fs::write(path, text).map_err(|error| failure(format!("cannot write {}: {error}", path.display())))
}

fn load(repo: &Path, ticket: &str, run: usize) -> Result<String, Failure> {
    let path = patch_path(repo, ticket, run);
    fs::read_to_string(&path).map_err(|error| failure(format!("the attempt of run {run} is missing ({}): {error}", path.display())))
}

/// Save the attempt of `run` and add the run to the log. The patch is written first, so a run is never logged without its attempt.
///
/// # Errors
///
/// [`Failure::Log`] when the patch cannot be made or written, or the log cannot be written.
pub fn log_run(repo: &Path, run: &Run) -> Result<(), Failure> {
    runlog::ensure_folder(repo)?;
    save(&patch_path(repo, &run.ticket, run.number), &snapshot(repo)?)?;
    runlog::append(repo, run)
}

/// The files a patch touches, from its `diff --git` lines.
fn patch_files(patch: &str) -> BTreeSet<String> {
    patch.lines().filter_map(|line| line.strip_prefix("diff --git ")?.rsplit_once(" b/")).map(|(_, name)| name.to_string()).collect()
}

/// What `git apply` is asked to do with a patch.
#[derive(Clone, Copy)]
enum How {
    /// Make the changes in the patch.
    Forward,
    /// Take the changes in the patch back out.
    Undo,
    /// Only check that the changes could be taken back out; touch nothing.
    CheckUndo,
}

fn apply(repo: &Path, how: How, patch: &str) -> Result<(), Failure> {
    if patch.trim().is_empty() {
        return Ok(());
    }
    let flags: &[&str] = match how {
        How::Forward => &[],
        How::Undo => &["-R"],
        How::CheckUndo => &["-R", "--check"],
    };
    let mut args = vec!["apply", "--whitespace=nowarn"];
    args.extend_from_slice(flags);
    args.push("-");
    git_input(repo, &args, patch)
}

/// Undo `current`, then apply `wanted`; when `wanted` does not apply, `current` is put back.
fn swap(repo: &Path, current: &str, wanted: &str) -> Result<(), Failure> {
    apply(repo, How::Undo, current)?;
    if let Err(error) = apply(repo, How::Forward, wanted) {
        apply(repo, How::Forward, current)?;
        return Err(error);
    }
    Ok(())
}

/// What a restore did (or would do).
#[derive(Debug, PartialEq, Eq)]
pub struct Outcome {
    pub files: Vec<String>,
    pub already: bool,
}

/// What to restore and how.
pub struct Request<'a> {
    pub repo: &'a Path,
    pub ticket: &'a str,
    pub runs: &'a [&'a Run],
    pub target: usize,
    pub dry_run: bool,
    pub force: bool,
}

fn check_target(request: &Request) -> Result<usize, Failure> {
    let latest = request.runs.last().ok_or_else(|| failure("no runs are logged for this ticket"))?;
    if !request.runs.iter().any(|run| run.number == request.target) {
        return Err(failure(format!("run {} is not in the log (this ticket has runs 0 to {})", request.target, latest.number)));
    }
    Ok(latest.number)
}

/// Put the working tree back to what it was at `request.target`.
///
/// The tree must still be exactly as the newest logged run left it, so nothing unsaved is lost (`force` skips that check, after saving what is
/// there to `before-restore.patch`). A restore that does not apply cleanly puts everything back and says why.
///
/// # Errors
///
/// [`Failure::Log`] when the run or its attempt is missing, the tree has changed since the last logged run, or git cannot apply a patch.
pub fn restore(request: &Request) -> Result<Outcome, Failure> {
    let latest = check_target(request)?;
    let current = snapshot(request.repo)?;
    if !request.force && current != load(request.repo, request.ticket, latest)? {
        return Err(failure(format!("the files changed after run {latest} was logged; log a run first (krypton scan . --log) or pass --force")));
    }
    let wanted = load(request.repo, request.ticket, request.target)?;
    if current == wanted {
        return Ok(Outcome { files: Vec::new(), already: true });
    }
    let files = patch_files(&current).into_iter().chain(patch_files(&wanted)).collect::<BTreeSet<_>>().into_iter().collect();
    if request.dry_run {
        apply(request.repo, How::CheckUndo, &current)?;
        return Ok(Outcome { files, already: false });
    }
    save(&runlog::folder(request.repo).join("attempts").join("before-restore.patch"), &current)?;
    swap(request.repo, &current, &wanted)?;
    Ok(Outcome { files, already: false })
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;

    static COUNTER: AtomicUsize = AtomicUsize::new(0);

    fn sh(repo: &Path, args: &[&str]) {
        git(repo, args, &[0]).unwrap();
    }

    fn repo_with_commit() -> PathBuf {
        let repo = std::env::temp_dir().join(format!("krypton-attempts-{}-{}", std::process::id(), COUNTER.fetch_add(1, Ordering::SeqCst)));
        fs::create_dir_all(&repo).unwrap();
        sh(&repo, &["init", "-q"]);
        sh(&repo, &["config", "user.email", "t@example.com"]);
        sh(&repo, &["config", "user.name", "t"]);
        fs::write(repo.join("a.py"), "x = 1\n").unwrap();
        sh(&repo, &["add", "."]);
        sh(&repo, &["commit", "-q", "-m", "start"]);
        repo
    }

    fn run(number: usize) -> Run {
        Run { number, time: 0, ticket: "t".to_string(), purity: 70, functions: std::collections::BTreeMap::new(), waived: std::collections::BTreeMap::new() }
    }

    /// Edit the tree, log it as `number`, and return nothing: the log and the attempt are on disk.
    fn log_state(repo: &Path, number: usize, a: &str, new_file: Option<&str>) {
        fs::write(repo.join("a.py"), a).unwrap();
        if let Some(text) = new_file {
            fs::write(repo.join("b.py"), text).unwrap();
        } else if repo.join("b.py").exists() {
            fs::remove_file(repo.join("b.py")).unwrap();
        }
        log_run(repo, &run(number)).unwrap();
    }

    fn request<'a>(repo: &'a Path, runs: &'a [&'a Run], target: usize) -> Request<'a> {
        Request { repo, ticket: "t", runs, target, dry_run: false, force: false }
    }

    // Logging a run makes the folder ignore itself, so the saved code cannot be committed.
    #[test]
    fn test_folder_ignored() {
        let repo = repo_with_commit();
        log_state(
            &repo, 0, "x = 1
", None,
        );
        git(&repo, &["check-ignore", "-q", ".krypton/attempts/t-run0.patch"], &[0]).unwrap();
        let status = git(&repo, &["status", "--short"], &[0]).unwrap();
        assert!(!String::from_utf8_lossy(&status).contains(".krypton"), "git does not see the folder at all");
    }

    // A restore brings back the edit and the new file of an earlier run exactly.
    #[test]
    fn test_restore_round_trip() {
        let repo = repo_with_commit();
        log_state(&repo, 0, "x = 1\n", None);
        log_state(&repo, 1, "x = 2\n", Some("y = 1\n"));
        log_state(&repo, 2, "x = 3\n", None);
        let runs = [run(0), run(1), run(2)];
        let all: Vec<&Run> = runs.iter().collect();
        let outcome = restore(&request(&repo, &all, 1)).unwrap();
        assert_eq!(fs::read_to_string(repo.join("a.py")).unwrap(), "x = 2\n", "the edit of run 1 is back");
        assert_eq!(fs::read_to_string(repo.join("b.py")).unwrap(), "y = 1\n", "the file run 1 added is back");
        assert_eq!(outcome.files, ["a.py", "b.py"], "both files were touched");
    }

    // A dry run says what it would do and leaves every file as it was.
    #[test]
    fn test_dry_run_changes_nothing() {
        let repo = repo_with_commit();
        log_state(
            &repo, 0, "x = 1
", None,
        );
        log_state(
            &repo,
            1,
            "x = 2
",
            Some(
                "y = 1
",
            ),
        );
        let runs = [run(0), run(1)];
        let all: Vec<&Run> = runs.iter().collect();
        let dry = Request { dry_run: true, ..request(&repo, &all, 0) };
        let outcome = restore(&dry).unwrap();
        assert_eq!(outcome.files, ["a.py", "b.py"], "it lists what it would touch");
        assert_eq!(
            fs::read_to_string(repo.join("a.py")).unwrap(),
            "x = 2
",
            "the edit is still there"
        );
        assert!(repo.join("b.py").exists(), "the new file is still there");
        restore(&request(&repo, &all, 0)).unwrap();
        assert_eq!(
            fs::read_to_string(repo.join("a.py")).unwrap(),
            "x = 1
",
            "the real restore still works afterwards"
        );
    }

    // Unsaved edits after the last logged run are not thrown away.
    #[test]
    fn test_restore_refuses_stale() {
        let repo = repo_with_commit();
        log_state(&repo, 0, "x = 1\n", None);
        log_state(&repo, 1, "x = 2\n", None);
        fs::write(repo.join("a.py"), "x = 99\n").unwrap();
        let runs = [run(0), run(1)];
        let all: Vec<&Run> = runs.iter().collect();
        let error = restore(&request(&repo, &all, 0)).unwrap_err();
        assert!(matches!(error, Failure::Log(text) if text.contains("changed after run 1")), "says the files changed");
        assert_eq!(fs::read_to_string(repo.join("a.py")).unwrap(), "x = 99\n", "the unsaved edit is untouched");
    }

    // The ticket becomes a short safe file name.
    #[test]
    fn test_slug() {
        assert_eq!(slug("Fix: discount/rounding!"), "fix--discount-rounding-", "letters kept, the rest a dash");
        assert_eq!(slug("???"), "ticket", "nothing left means a default name");
    }

    // The files of a patch come from its diff lines, new files included.
    #[test]
    fn test_patch_files() {
        let patch = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\ndiff --git a/new.py b/new.py\nnew file mode 100644\n";
        assert_eq!(patch_files(patch).into_iter().collect::<Vec<_>>(), ["new.py", "src/a.py"], "both files, sorted");
    }
}
