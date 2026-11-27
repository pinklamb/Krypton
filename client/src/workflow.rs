//! The GitHub Actions workflow `krypton setup` can write for a repository: `.github/workflows/krypton.yml`, the Krypton gate on every pull
//! request. It is an ordinary file in the repository (commit it to turn the gate on). Running setup again with another gate or target
//! rewrites a file setup wrote, so old rules never linger; a file whose `Written by krypton setup` line was removed is yours and is never
//! touched.

use std::fs;
use std::path::{Path, PathBuf};

use crate::Failure;

/// The line that marks a workflow as setup's own, to rewrite when the gate changes. Removing it keeps a hand-edited workflow as it is.
pub const OURS: &str = "# Written by `krypton setup`";

/// Where the workflow goes, relative to the repository.
pub const WORKFLOW: &str = ".github/workflows/krypton.yml";

/// Where a person installs the Krypton GitHub App on a repository. With it, the gate's comment and status line come from Krypton (its
/// name and crystal); without it they come from github-actions, with a note pointing here.
pub const APP_INSTALL: &str = "https://github.com/apps/kr-ypton/installations/new";

/// What fails a pull request.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Gate {
    /// No workflow is written.
    Off,
    /// A pull request fails only if it lowers the purity score, so old code never blocks anyone.
    Worse,
    /// A pull request fails if the files it changes score below the target; the rest of the codebase is never judged.
    Target,
}

impl Gate {
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Worse => "worse",
            Self::Target => "target",
        }
    }

    #[must_use]
    pub fn parse(text: &str) -> Option<Self> {
        match text.trim().to_ascii_lowercase().as_str() {
            "off" | "1" | "no" | "none" => Some(Self::Off),
            "worse" | "2" | "never-worse" => Some(Self::Worse),
            "target" | "3" => Some(Self::Target),
            _ => None,
        }
    }
}

/// The workflow for a gate, or `None` for [`Gate::Off`].
#[must_use]
pub fn yaml(gate: Gate, target: i64) -> Option<String> {
    let rule = match gate {
        Gate::Off => return None,
        Gate::Worse => "          fail-on-worse: \"true\"   # a pull request fails only if it lowers the purity score\n".to_string(),
        Gate::Target => format!("          target: \"{target}\"   # a pull request fails if the files it changes score below this\n"),
    };
    Some(format!(
        "# The Krypton gate: one line in each pull request's checks, and the scorecard in one pull request comment that is edited on every push.
# Written by `krypton setup`, which rewrites it when you change the gate. To keep your own edits, delete this line. Commit it to turn the gate on.
name: Krypton
on: pull_request
permissions:
  contents: read    # read the code
  statuses: write   # set the Krypton gate line
  pull-requests: write   # keep the one Krypton comment on the pull request up to date
  id-token: write   # log in to Krypton as this repository, so there is no secret to store
jobs:
  krypton:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: pinklamb/Krypton@main
        with:
{rule}          # on-server-error: \"pass\"   # uncomment so a Krypton server outage never blocks a pull request (your code failing is always red)
"
    ))
}

/// What happened to the workflow file.
#[derive(Debug, PartialEq, Eq)]
pub enum Written {
    /// It was written here for the first time.
    Wrote(PathBuf),
    /// Setup's earlier workflow had another gate or target, and was rewritten with the new one.
    Updated(PathBuf),
    /// Setup's workflow already has exactly this gate.
    Unchanged(PathBuf),
    /// A workflow without setup's line (your own) was left exactly as it was.
    Kept(PathBuf),
    /// No gate was chosen, but setup's workflow from an earlier run is still there and still gates pull requests.
    StillOn(PathBuf),
    /// No gate was chosen, and there is no workflow.
    Off,
}

/// Write the workflow for `gate` into `repo`: the first time, or again when setup's own workflow has another gate or target. A workflow
/// without setup's line is never touched, and choosing no gate never deletes anything (it says when an old workflow still gates).
///
/// # Errors
///
/// [`Failure::Read`] or [`Failure::Log`] when the file cannot be read or written.
pub fn write(repo: &Path, gate: Gate, target: i64) -> Result<Written, Failure> {
    let path = repo.join(WORKFLOW);
    let existing = if path.exists() { Some(fs::read_to_string(&path).map_err(|error| Failure::Read(path.display().to_string(), error))?) } else { None };
    let ours = existing.as_deref().is_some_and(|text| text.contains(OURS));
    let Some(text) = yaml(gate, target) else {
        return Ok(if ours { Written::StillOn(path) } else { Written::Off });
    };
    match existing {
        Some(_) if !ours => return Ok(Written::Kept(path)),
        Some(old) if old == text => return Ok(Written::Unchanged(path)),
        Some(_) => {
            fs::write(&path, text).map_err(|error| Failure::Log(format!("cannot write {}: {error}", path.display())))?;
            return Ok(Written::Updated(path));
        }
        None => {}
    }
    let folder = path.parent().unwrap_or(repo);
    fs::create_dir_all(folder).map_err(|error| Failure::Log(format!("cannot create {}: {error}", folder.display())))?;
    fs::write(&path, text).map_err(|error| Failure::Log(format!("cannot write {}: {error}", path.display())))?;
    Ok(Written::Wrote(path))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Each gate writes its own rule, and off writes nothing.
    #[test]
    fn test_yaml() {
        let worse = yaml(Gate::Worse, 70).unwrap();
        assert!(worse.contains("fail-on-worse: \"true\"") && !worse.contains("target:"), "never-worse rule only");
        assert!(worse.contains("id-token: write") && worse.contains("fetch-depth: 0"), "permissions and history for the base");
        assert!(worse.contains("pull-requests: write"), "the comment permission");
        assert!(worse.contains("# on-server-error: \"pass\""), "the outage switch, off until uncommented");
        let target = yaml(Gate::Target, 72).unwrap();
        assert!(target.contains("target: \"72\"") && !target.contains("fail-on-worse"), "target rule only");
        assert!(yaml(Gate::Off, 70).is_none(), "off writes nothing");
    }

    // Setup's workflow follows the gate: written once, rewritten when the gate or target changes, left alone when it does not.
    #[test]
    fn test_write() {
        let repo = std::env::temp_dir().join(format!("krypton-workflow-{}", std::process::id()));
        std::fs::create_dir_all(&repo).unwrap();
        assert_eq!(write(&repo, Gate::Off, 70).unwrap(), Written::Off, "off");
        assert!(matches!(write(&repo, Gate::Target, 70).unwrap(), Written::Wrote(_)), "written");
        assert!(matches!(write(&repo, Gate::Target, 70).unwrap(), Written::Unchanged(_)), "same gate: untouched");
        assert!(matches!(write(&repo, Gate::Off, 70).unwrap(), Written::StillOn(_)), "off says the old gate still runs");
        std::fs::remove_dir_all(&repo).unwrap();
    }

    // Running setup again with another target or gate rewrites setup's workflow, and the old rule is gone.
    #[test]
    fn test_write_updates() {
        let repo = std::env::temp_dir().join(format!("krypton-workflow-new-{}", std::process::id()));
        std::fs::create_dir_all(&repo).unwrap();
        let now = || std::fs::read_to_string(repo.join(WORKFLOW)).unwrap();
        write(&repo, Gate::Target, 70).unwrap();
        assert!(matches!(write(&repo, Gate::Target, 75).unwrap(), Written::Updated(_)) && now().contains("target: \"75\""), "new target");
        assert!(matches!(write(&repo, Gate::Worse, 75).unwrap(), Written::Updated(_)) && !now().contains("target:"), "new gate, old rule gone");
        std::fs::remove_dir_all(&repo).unwrap();
    }

    // A workflow without setup's line is yours: never rewritten, whatever the gate.
    #[test]
    fn test_write_keeps_yours() {
        let repo = std::env::temp_dir().join(format!("krypton-workflow-yours-{}", std::process::id()));
        std::fs::create_dir_all(repo.join(".github/workflows")).unwrap();
        std::fs::write(repo.join(WORKFLOW), "name: mine").unwrap();
        assert!(matches!(write(&repo, Gate::Worse, 70).unwrap(), Written::Kept(_)), "kept");
        assert!(matches!(write(&repo, Gate::Off, 70).unwrap(), Written::Off), "off: nothing to say about yours");
        assert_eq!(std::fs::read_to_string(repo.join(WORKFLOW)).unwrap(), "name: mine", "untouched");
        std::fs::remove_dir_all(&repo).unwrap();
    }

    // The answers people type are understood.
    #[test]
    fn test_parse_gate() {
        assert_eq!(
            [Gate::parse("2"), Gate::parse("target"), Gate::parse("no"), Gate::parse("x")],
            [Some(Gate::Worse), Some(Gate::Target), Some(Gate::Off), None],
            "numbers and names"
        );
    }
}
