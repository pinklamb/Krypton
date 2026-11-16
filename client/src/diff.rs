//! `scan --base REF`: which files changed since a git ref, and which of their lines were added or changed (1-based). Only those lines are
//! scored, so old tangle in a file a branch touched does not block it. Read from `git diff -w --unified=0`, the same as the line numbers the
//! earlier command line gave.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::process::Command;

use crate::Failure;

/// Added line numbers per file.
pub type Altered = BTreeMap<String, BTreeSet<u32>>;

fn git(root: &Path, args: &[&str]) -> Option<Vec<u8>> {
    let done = Command::new("git").arg("-C").arg(root).args(args).output().ok()?;
    done.status.success().then_some(done.stdout)
}

/// The files changed since `base` (and the new, untracked ones among `files`) with the lines added in each.
///
/// # Errors
///
/// [`Failure::Usage`] when git cannot diff against `base` (not a repository, or no such ref).
pub fn changed_since(base: &str, root: &Path, files: &[String]) -> Result<Altered, Failure> {
    let args = [
        "-c",
        "core.quotepath=off",
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "-w",
        "--unified=0",
        "--relative",
        base,
        "--",
        ".",
    ];
    let diff = git(root, &args)
        .ok_or_else(|| Failure::Usage(format!("git diff against '{base}' failed; is {} inside a git repository, and is '{base}' a ref?", root.display())))?;
    let mut altered = added_lines(&String::from_utf8_lossy(&diff));
    let untracked = git(root, &["ls-files", "--others", "--exclude-standard", "-z"]).unwrap_or_default();
    for name in untracked.split(|byte| *byte == 0).map(String::from_utf8_lossy) {
        if files.iter().any(|file| file.as_str() == name) {
            let lines = std::fs::read(root.join(name.as_ref())).map_or(0, |bytes| bytes.split(|byte| *byte == b'\n').count().saturating_sub(1));
            altered.insert(name.into_owned(), (1..=u32::try_from(lines.saturating_add(1)).unwrap_or(u32::MAX)).collect());
        }
    }
    Ok(altered)
}

/// `(removed, start, added)` from a hunk header `@@ -a[,b] +c[,d] @@`; a missing count means 1.
fn hunk(line: &str) -> Option<(u32, u32, u32)> {
    let body = line.strip_prefix("@@ -")?;
    let (old, rest) = body.split_once(" +")?;
    let new = rest.split_once(" @@")?.0;
    let count = |text: &str| text.split_once(',').map_or(Some(1), |(_, count)| count.parse().ok());
    let start = new.split_once(',').map_or(new, |(start, _)| start).parse().ok()?;
    Some((count(old)?, start, count(new)?))
}

/// Reads a `--unified=0` diff line by line. A hunk header says how many body lines follow, so body lines are consumed by count and never
/// mistaken for headers (an added line `++ x` shows up as `+++ x`).
#[derive(Default)]
struct Reader {
    altered: Altered,
    current: Option<String>,
    in_header: bool,
    owed: u32,
}

impl Reader {
    fn feed(&mut self, line: &str) {
        if self.owed > 0 {
            // a line starting with a backslash ("\ No newline at end of file") belongs to the hunk but is not one of its lines
            self.owed = if line.starts_with('\\') { self.owed } else { self.owed.saturating_sub(1) };
        } else if line.starts_with("diff --git ") {
            (self.current, self.in_header) = (None, true);
        } else if self.in_header && line.starts_with("+++ ") {
            self.open_file(line);
        } else if let Some(found) = hunk(line) {
            self.add_hunk(found);
        }
    }

    fn open_file(&mut self, header: &str) {
        let target = header.trim_start_matches("+++ ").trim_end_matches('\t');
        self.current = (target != "/dev/null" && !target.starts_with('"')).then(|| target.strip_prefix("b/").unwrap_or(target).to_string());
        if let Some(name) = &self.current {
            self.altered.entry(name.clone()).or_default();
        }
    }

    fn add_hunk(&mut self, (removed, start, added): (u32, u32, u32)) {
        self.in_header = false;
        self.owed = removed.saturating_add(added);
        if let Some(lines) = self.current.as_ref().and_then(|name| self.altered.get_mut(name)) {
            lines.extend(start..start.saturating_add(added));
        }
    }
}

/// Added line numbers per file from a `--unified=0` diff.
#[must_use]
pub fn added_lines(diff: &str) -> Altered {
    let mut reader = Reader::default();
    diff.lines().for_each(|line| reader.feed(line));
    reader.altered
}

#[cfg(test)]
mod tests {
    use super::*;

    const DIFF: &str = "diff --git a/src/a.py b/src/a.py\nindex 1..2 100644\n--- a/src/a.py\n+++ b/src/a.py\n@@ -3,2 +3,3 @@ def f():\n-old one\n-old two\n+new one\n+++ looks like a header\n+new three\n@@ -10 +11 @@\n-x\n+y\ndiff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n";

    // Added lines come from the hunks and a body line that looks like a header is not one.
    #[test]
    fn test_added_lines() {
        let altered = added_lines(DIFF);
        assert_eq!(
            altered.get("src/a.py").map(|lines| lines.iter().copied().collect::<Vec<_>>()),
            Some(vec![3, 4, 5, 11]),
            "the added line `+++ looks like a header` is a body line of the hunk (line 4), not the start of a new file"
        );
        assert!(!altered.contains_key("gone.py"), "a deleted file has nothing to score");
    }

    // A hunk header with missing counts means one line.
    #[test]
    fn test_hunk_header() {
        assert_eq!(hunk("@@ -10 +11 @@ context"), Some((1, 11, 1)));
        assert_eq!(hunk("@@ -3,2 +3,0 @@"), Some((2, 3, 0)));
        assert_eq!(hunk("not a hunk"), None);
    }
}
