//! The git pre-push hook `krypton setup` can install: before every `git push` on this computer, Krypton scores the commits being pushed
//! with the pull request gate's rule and stops a push that breaks it (`krypton prepush`). It goes where git looks for hooks (`.git/hooks`,
//! or `core.hooksPath`), so it is never committed and only checks pushes made from this clone. Like the workflow (`workflow.rs`), a hook
//! setup wrote carries its line and is rewritten or removed as the setting changes; a pre-push hook of your own is never touched.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::workflow::OURS;
use crate::Failure;

/// The hook. It runs on your computer, where `krypton` is a shell function a hook cannot see, so it calls the image the way that function
/// does, handing git's lines to `krypton prepush`. Only exit 1 (the code broke the gate) stops the push; anything else lets it through.
const SCRIPT: &str = r#"#!/bin/sh
# Written by `krypton setup`: before each git push, Krypton scores the commits being pushed and stops the push if they break the gate.
# Skip it once with: git push --no-verify. Turn it off with: krypton setup --hook off (or delete this file).
image="${KRYPTON_IMAGE:-krypton}"
here="$(git rev-parse --show-toplevel)"
command -v cygpath >/dev/null 2>&1 && here="$(cygpath -w "$here")"
if ! command -v docker >/dev/null 2>&1; then
  echo "krypton: Docker is not installed, so this push was not checked." >&2
  exit 0
fi
MSYS_NO_PATHCONV=1 docker run --rm -i --pull never -v krypton-home:/krypton-home -v "$here:/repo" "$image" prepush "$@"
status=$?
if [ "$status" -eq 1 ]; then exit 1; fi
if [ "$status" -ne 0 ]; then echo "krypton: could not check this push (exit $status), so it goes ahead unchecked." >&2; fi
exit 0
"#;

/// What happened to the hook.
#[derive(Debug, PartialEq, Eq)]
pub enum Hook {
    /// Written for the first time.
    Wrote(PathBuf),
    /// Setup's hook was already there, exactly as it would be written.
    Unchanged(PathBuf),
    /// Setup's older hook was rewritten.
    Updated(PathBuf),
    /// A pre-push hook of your own is there, so nothing was written.
    Kept(PathBuf),
    /// The check was turned off, and setup's hook was removed.
    Removed(PathBuf),
    /// The check is off and there is no hook of setup's.
    Off,
    /// The folder is not a git checkout, so there is nothing to hook into.
    NotGit,
}

/// Where git runs this repository's pre-push hook from, following `core.hooksPath`.
fn hook_path(repo: &Path) -> Option<PathBuf> {
    let done = Command::new("git").arg("-C").arg(repo).args(["rev-parse", "--git-path", "hooks/pre-push"]).output().ok()?;
    if !done.status.success() {
        return None;
    }
    let path = PathBuf::from(String::from_utf8_lossy(&done.stdout).trim());
    Some(if path.is_absolute() { path } else { repo.join(path) })
}

#[cfg(unix)]
fn make_runnable(path: &Path) -> Result<(), Failure> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).map_err(|error| Failure::Log(format!("cannot make {} runnable: {error}", path.display())))
}

#[cfg(not(unix))]
#[allow(clippy::unnecessary_wraps, reason = "the same signature as the unix version; Windows runs hooks without an executable bit")]
const fn make_runnable(_path: &Path) -> Result<(), Failure> {
    Ok(())
}

fn put(path: &Path) -> Result<(), Failure> {
    if let Some(folder) = path.parent() {
        fs::create_dir_all(folder).map_err(|error| Failure::Log(format!("cannot create {}: {error}", folder.display())))?;
    }
    fs::write(path, SCRIPT).map_err(|error| Failure::Log(format!("cannot write {}: {error}", path.display())))?;
    make_runnable(path)
}

/// Install, keep, update or remove the hook so it matches `on`. A hook without setup's line is yours and is never written over or removed.
///
/// # Errors
///
/// [`Failure::Read`] or [`Failure::Log`] when the hook cannot be read, written or removed.
pub fn write(repo: &Path, on: bool) -> Result<Hook, Failure> {
    let Some(path) = hook_path(repo) else { return Ok(Hook::NotGit) };
    let existing = if path.exists() { Some(fs::read_to_string(&path).map_err(|error| Failure::Read(path.display().to_string(), error))?) } else { None };
    let ours = existing.as_deref().is_some_and(|text| text.contains(OURS));
    match (on, existing) {
        (false, Some(_)) if ours => {
            fs::remove_file(&path).map_err(|error| Failure::Log(format!("cannot remove {}: {error}", path.display())))?;
            Ok(Hook::Removed(path))
        }
        (false, _) => Ok(Hook::Off),
        (true, Some(_)) if !ours => Ok(Hook::Kept(path)),
        (true, Some(text)) if text == SCRIPT => Ok(Hook::Unchanged(path)),
        (true, Some(_)) => put(&path).map(|()| Hook::Updated(path)),
        (true, None) => put(&path).map(|()| Hook::Wrote(path)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repo(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("krypton-hook-{name}-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let done = Command::new("git").arg("-C").arg(&root).args(["init", "-q"]).status().unwrap();
        assert!(done.success(), "git init");
        root
    }

    // The hook is written once, runs krypton prepush, and is left alone when it is already right.
    #[test]
    fn test_hook_on() {
        let root = repo("on");
        assert!(matches!(write(&root, true).unwrap(), Hook::Wrote(_)), "written");
        assert!(fs::read_to_string(hook_path(&root).unwrap()).unwrap().contains("prepush"), "it runs krypton prepush");
        assert!(matches!(write(&root, true).unwrap(), Hook::Unchanged(_)), "already right");
        fs::remove_dir_all(&root).unwrap();
    }

    // Turning the check off removes setup's hook, and turning it off again has nothing left to do.
    #[test]
    fn test_hook_off() {
        let root = repo("off");
        write(&root, true).unwrap();
        let path = hook_path(&root).unwrap();
        assert!(matches!(write(&root, false).unwrap(), Hook::Removed(_)) && !path.exists(), "removed when off");
        assert_eq!(write(&root, false).unwrap(), Hook::Off, "off and gone");
        fs::remove_dir_all(&root).unwrap();
    }

    // A pre-push hook of your own is never written over or removed.
    #[test]
    fn test_hook_keeps_yours() {
        let root = repo("yours");
        let path = hook_path(&root).unwrap();
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, "#!/bin/sh\necho mine\n").unwrap();
        assert!(matches!(write(&root, true).unwrap(), Hook::Kept(_)), "kept");
        assert_eq!(write(&root, false).unwrap(), Hook::Off, "not removed");
        assert_eq!(fs::read_to_string(&path).unwrap(), "#!/bin/sh\necho mine\n", "untouched");
        fs::remove_dir_all(&root).unwrap();
    }

    // A folder that is not a git checkout has nowhere to put a hook.
    #[test]
    fn test_hook_not_git() {
        let root = std::env::temp_dir().join(format!("krypton-hook-plain-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join(".git"), "not a repository").unwrap();
        assert_eq!(write(&root, true).unwrap(), Hook::NotGit, "not git");
        fs::remove_dir_all(&root).unwrap();
    }
}
