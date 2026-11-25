//! What kind of change each touched function had: the code at the base and at the head of a change, compared function by function.
//!
//! * new: not at the base, and no deleted function with the same body;
//! * renamed or moved: a function deleted at the base reappears with the same body (same fingerprint), under a new name in the same file
//!   or in another file;
//! * parameters changed: its parameter list differs, so its callers may have to change too;
//! * logic changed: its tokens differ;
//! * comments or layout only: same tokens, so nothing downstream can be affected;
//! * deleted: at the base, gone at the head, and not renamed or moved.

use std::collections::{BTreeMap, BTreeSet, HashSet};

use super::graph::{Function, Graph};

/// The kind of change one function had.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Change {
    New,
    Logic,
    Parameters,
    Cosmetic,
    /// From the old name.
    Renamed(String),
    /// From the old file.
    Moved(String),
}

impl Change {
    /// How the change is written next to the function.
    #[must_use]
    pub fn word(&self) -> String {
        match self {
            Self::New => "new".to_string(),
            Self::Logic => "logic changed".to_string(),
            Self::Parameters => "parameters changed".to_string(),
            Self::Cosmetic => "comments or layout only".to_string(),
            Self::Renamed(from) => format!("renamed from {from}"),
            Self::Moved(from) => format!("moved from {from}"),
        }
    }
}

/// Every touched function's kind of change, by graph id at the head, and the functions the change deleted, by key.
#[derive(Debug, Default)]
pub struct Changes {
    pub kinds: BTreeMap<usize, Change>,
    pub deleted: Vec<String>,
}

fn is_function(function: &Function) -> bool {
    !function.is_top_level() && !function.is_route
}

fn compare_one(was: &Function, now: &Function) -> Change {
    if was.params != now.params {
        Change::Parameters
    } else if was.fingerprint == now.fingerprint {
        Change::Cosmetic
    } else {
        Change::Logic
    }
}

/// A function not at the base: renamed or moved when a deleted function had the same body, else new.
fn moved_or_new(now: &Function, deleted: &mut Vec<&Function>) -> Change {
    let Some(at) = deleted.iter().position(|old| old.fingerprint == now.fingerprint) else { return Change::New };
    let old = deleted.remove(at);
    if old.file == now.file {
        Change::Renamed(old.name.clone())
    } else {
        Change::Moved(old.file.clone())
    }
}

/// Compare the touched functions of `head` (by id, in `changed`) with `base`, a graph of the same files at the base of the change.
#[must_use]
pub fn compare(head: &Graph, base: &Graph, changed: &BTreeSet<usize>) -> Changes {
    let head_keys: HashSet<String> = head.all().filter(|(_, function)| is_function(function)).map(|(_, function)| function.key()).collect();
    let mut deleted: Vec<&Function> =
        base.all().map(|(_, function)| function).filter(|function| is_function(function) && !head_keys.contains(&function.key())).collect();
    let mut kinds = BTreeMap::new();
    for &id in changed {
        let Some(now) = head.function(id).filter(|function| is_function(function)) else { continue };
        let was = base.find(&now.key()).and_then(|found| base.function(found));
        let change = was.map_or_else(|| moved_or_new(now, &mut deleted), |was| compare_one(was, now));
        kinds.insert(id, change);
    }
    Changes { kinds, deleted: deleted.iter().map(|function| function.key()).collect() }
}

#[cfg(test)]
mod tests {
    use super::super::from_sources;
    use super::*;

    fn graph(files: &[(&str, &str)]) -> Graph {
        from_sources(&files.iter().map(|(path, source)| ((*path).to_string(), (*source).to_string())).collect::<Vec<_>>()).unwrap()
    }

    // Each kind of change, from one base and one head.
    #[test]
    fn test_kinds() {
        let base = graph(&[
            ("a.py", "def same(x):\n    return x\n\ndef logic(x):\n    return x\n\ndef params(x):\n    return x\n\ndef old_name(x):\n    return x * 3\n\ndef gone():\n    pass\n"),
            ("b.py", "def leaving(y):\n    return y - 7\n"),
        ]);
        let head = graph(&[
            ("a.py", "def same(x):\n    # why\n    return x\n\ndef logic(x):\n    return x + 1\n\ndef params(x, y):\n    return x\n\ndef new_name(x):\n    return x * 3\n\ndef fresh():\n    return 1\n"),
            ("b.py", ""),
            ("c.py", "def leaving(y):\n    return y - 7\n"),
        ]);
        let keys = ["a.py::same", "a.py::logic", "a.py::params", "a.py::new_name", "a.py::fresh", "c.py::leaving"];
        let changed: BTreeSet<usize> = keys.iter().map(|key| head.find(key).unwrap()).collect();
        let found = compare(&head, &base, &changed);
        let kind = |key: &str| found.kinds.get(&head.find(key).unwrap()).cloned();
        assert_eq!(kind("a.py::same"), Some(Change::Cosmetic));
        assert_eq!(kind("a.py::logic"), Some(Change::Logic));
        assert_eq!(kind("a.py::params"), Some(Change::Parameters));
        assert_eq!(kind("a.py::new_name"), Some(Change::Renamed("old_name".into())));
        assert_eq!(kind("c.py::leaving"), Some(Change::Moved("b.py".into())));
        assert_eq!(kind("a.py::fresh"), Some(Change::New), "a different body from gone()");
        assert_eq!(found.deleted, ["a.py::gone"], "only the function that really went");
    }
}
