//! The GitHub Actions workflow `krypton setup` can write for a repository: `.github/workflows/krypton.yml`, the Krypton gate on every pull
//! request. It is an ordinary file in the repository (commit it to turn the gate on), and setup never overwrites one that exists.

use std::fs;
use std::path::{Path, PathBuf};

use crate::Failure;

/// Where the workflow goes, relative to the repository.
pub const WORKFLOW: &str = ".github/workflows/krypton.yml";

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
# Written by `krypton setup`; edit it freely, setup never overwrites it. Commit it to turn the gate on.
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
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: pinklamb/Krypton@main
        with:
{rule}"
    ))
}

/// What happened to the workflow file.
#[derive(Debug, PartialEq, Eq)]
pub enum Written {
    /// It was written here.
    Wrote(PathBuf),
    /// A workflow was already there and was left exactly as it was.
    Kept(PathBuf),
    /// No gate was chosen.
    Off,
}

/// Write the workflow for `gate` into `repo`, unless the gate is off or a workflow is already there.
///
/// # Errors
///
/// [`Failure::Log`] when the folder or the file cannot be written.
pub fn write(repo: &Path, gate: Gate, target: i64) -> Result<Written, Failure> {
    let Some(text) = yaml(gate, target) else { return Ok(Written::Off) };
    let path = repo.join(WORKFLOW);
    if path.exists() {
        return Ok(Written::Kept(path));
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
        let target = yaml(Gate::Target, 72).unwrap();
        assert!(target.contains("target: \"72\"") && !target.contains("fail-on-worse"), "target rule only");
        assert!(yaml(Gate::Off, 70).is_none(), "off writes nothing");
    }

    // The workflow is written once and an existing one is never touched.
    #[test]
    fn test_write() {
        let repo = std::env::temp_dir().join(format!("krypton-workflow-{}", std::process::id()));
        std::fs::create_dir_all(&repo).unwrap();
        assert_eq!(write(&repo, Gate::Off, 70).unwrap(), Written::Off, "off");
        assert!(matches!(write(&repo, Gate::Target, 70).unwrap(), Written::Wrote(_)), "written");
        std::fs::write(repo.join(WORKFLOW), "mine").unwrap();
        assert!(matches!(write(&repo, Gate::Worse, 70).unwrap(), Written::Kept(_)), "kept");
        assert_eq!(std::fs::read_to_string(repo.join(WORKFLOW)).unwrap(), "mine", "and left untouched");
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
