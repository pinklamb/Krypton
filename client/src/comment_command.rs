//! `krypton comment BEFORE.json AFTER.json [--badge FILE] [--fail-if-worse]` writes the pull request comment (markdown) comparing two summaries
//! made by `krypton scan --summary`. `krypton badge SUMMARY.json OUT.json` writes the README badge as a shields.io endpoint file. Only names and
//! numbers are used; no source text is involved.

use std::cmp::Ordering;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::runlog::{self, Run};
use crate::Failure;

/// A hidden line at the top of the comment, so CI can find it and edit it in place instead of adding another.
pub const MARKER: &str = "<!-- krypton-comment -->";
const LISTED: usize = 10;
const USAGE: &str = "usage: krypton comment BEFORE.json AFTER.json [--badge FILE] [--fail-if-worse] | krypton badge SUMMARY.json OUT.json";

fn headline(before: i64, after: i64) -> String {
    match after.cmp(&before) {
        Ordering::Greater => format!("▲ {before}% → {after}% (+{})", after.saturating_sub(before)),
        Ordering::Less => format!("▼ {before}% → {after}% (−{})", before.saturating_sub(after)),
        Ordering::Equal => format!("● {after}% unchanged"),
    }
}

/// A function this change made worse: new over the limit, or more times over than before.
struct Worse {
    key: String,
    before: Option<f64>,
    after: f64,
}

fn growth(worse: &Worse) -> f64 {
    worse.after - worse.before.unwrap_or(1.0)
}

fn worse_functions(before: &Run, after: &Run) -> Vec<Worse> {
    let grown = |key: &String, now: &f64| {
        let was = before.functions.get(key).copied();
        was.is_none_or(|earlier| *now > earlier).then(|| Worse { key: key.clone(), before: was, after: *now })
    };
    let mut found: Vec<Worse> = after.functions.iter().filter_map(|(key, now)| grown(key, now)).collect();
    found.sort_by(|left, right| growth(right).total_cmp(&growth(left)));
    found
}

fn times(value: f64) -> String {
    if (value - value.round()).abs() < 1e-9 {
        format!("{value:.0}×")
    } else {
        format!("{value:.1}×")
    }
}

fn line(worse: &Worse) -> String {
    let (file, function) = worse.key.split_once("::").unwrap_or((worse.key.as_str(), ""));
    let was = worse.before.map_or_else(|| "under the limit".to_string(), times);
    format!("- `{file} · {function}` {was} → {}", times(worse.after))
}

fn worse_section(list: &[Worse]) -> String {
    let noun = if list.len() == 1 { "function" } else { "functions" };
    let mut lines = vec![format!("**{} {noun} got worse**", list.len())];
    lines.extend(list.iter().take(LISTED).map(line));
    if list.len() > LISTED {
        lines.push(format!("- …and {} more", list.len().saturating_sub(LISTED)));
    }
    lines.join(
        "
",
    ) + "
"
}

/// The pull request comment.
#[must_use]
pub fn markdown(before: &Run, after: &Run) -> String {
    let list = worse_functions(before, after);
    let mut text = format!(
        "{MARKER}\n### Krypton · {}\n\n| | Before | After |\n|---|---|---|\n| Purity | {}% | {}% |\n| Over the limit | {} | {} |\n\n",
        headline(before.purity, after.purity),
        before.purity,
        after.purity,
        before.functions.len(),
        after.functions.len()
    );
    if !list.is_empty() {
        text.push_str(&worse_section(&list));
    } else if after.purity > before.purity {
        text.push_str("Nice: this change made the code simpler.\n");
    } else {
        text.push_str("No functions got worse.\n");
    }
    text
}

const fn color(purity: i64) -> &'static str {
    if purity >= 80 {
        "brightgreen"
    } else if purity >= 60 {
        "yellow"
    } else {
        "red"
    }
}

/// The README badge, in the shape shields.io reads from a URL (`https://img.shields.io/endpoint?url=...`).
#[must_use]
pub fn badge(after: &Run) -> Value {
    json!({ "schemaVersion": 1, "label": "krypton", "message": format!("{}%", after.purity), "color": color(after.purity) })
}

fn write(path: &Path, text: &str) -> Result<(), Failure> {
    fs::write(path, text).map_err(|error| Failure::Log(format!("{}: {error}", path.display())))
}

fn write_badge(path: &Path, after: &Run) -> Result<(), Failure> {
    write(path, &serde_json::to_string_pretty(&badge(after)).map_err(|error| Failure::Output(error.to_string()))?)
}

struct Options {
    files: Vec<PathBuf>,
    badge: Option<PathBuf>,
    fail_if_worse: bool,
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let mut options = Options { files: Vec::new(), badge: None, fail_if_worse: false };
    let mut words = args.iter();
    while let Some(word) = words.next() {
        match word.as_str() {
            "--fail-if-worse" => options.fail_if_worse = true,
            "--badge" => options.badge = Some(PathBuf::from(words.next().ok_or_else(|| Failure::Usage(format!("--badge needs a file\n{USAGE}")))?)),
            flag if flag.starts_with("--") => return Err(Failure::Usage(format!("unknown option {flag}\n{USAGE}"))),
            path => options.files.push(PathBuf::from(path)),
        }
    }
    Ok(options)
}

pub fn comment(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let [before_path, after_path] = options.files.as_slice() else { return Err(Failure::Usage(format!("give the summary before and after\n{USAGE}"))) };
    let (before, after) = (runlog::read_summary(before_path)?, runlog::read_summary(after_path)?);
    print!("{}", markdown(&before, &after));
    if let Some(path) = &options.badge {
        write_badge(path, &after)?;
    }
    if options.fail_if_worse && after.purity < before.purity {
        return Err(Failure::Flagged);
    }
    Ok(())
}

pub fn badge_command(args: &[String]) -> Result<(), Failure> {
    let [summary, out] = args else { return Err(Failure::Usage(USAGE.to_string())) };
    write_badge(Path::new(out), &runlog::read_summary(Path::new(summary))?)
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;

    fn run(purity: i64, functions: &[(&str, f64)]) -> Run {
        Run {
            number: 0,
            time: 0,
            ticket: String::new(),
            purity,
            functions: functions.iter().map(|(key, value)| ((*key).to_string(), *value)).collect::<BTreeMap<_, _>>(),
        }
    }

    // A higher score gets an up arrow, the table, and a kind word when nothing got worse.
    #[test]
    fn test_comment_better() {
        let text = markdown(&run(71, &[("a.py::f", 3.0), ("a.py::g", 2.0)]), &run(74, &[("a.py::f", 2.0)]));
        assert!(text.starts_with(MARKER), "the marker comes first so CI can find the comment");
        assert!(text.contains("### Krypton · ▲ 71% → 74% (+3)"), "arrow headline");
        assert!(text.contains("| Over the limit | 2 | 1 |"), "over-the-limit counts");
        assert!(text.contains("Nice: this change made the code simpler."), "a kind word");
    }

    // Only the functions the change made worse are listed, the biggest growth first, and a new one says it was under the limit.
    #[test]
    fn test_comment_worse() {
        let text = markdown(&run(74, &[("a.py::f", 3.0)]), &run(70, &[("a.py::f", 4.5), ("b.ts::g", 2.0)]));
        assert!(text.contains("### Krypton · ▼ 74% → 70% (−4)"), "down arrow");
        assert!(text.contains("**2 functions got worse**"), "the count");
        assert!(text.contains("- `b.ts · g` under the limit → 2×"), "a new over-limit function");
        assert!(text.contains("- `a.py · f` 3× → 4.5×"), "a function that grew");
    }

    // No change reads as unchanged, and a function that did not grow is not listed.
    #[test]
    fn test_comment_same() {
        let text = markdown(&run(74, &[("a.py::f", 3.0)]), &run(74, &[("a.py::f", 3.0)]));
        assert!(text.contains("● 74% unchanged"), "dot headline");
        assert!(text.contains("No functions got worse."), "nothing worse");
    }

    // The badge follows the purity band.
    #[test]
    fn test_badge_colors() {
        assert_eq!(badge(&run(85, &[])).get("color"), Some(&json!("brightgreen")), "80 and up");
        assert_eq!(badge(&run(70, &[])).get("color"), Some(&json!("yellow")), "60 to 79");
        assert_eq!(badge(&run(40, &[])).get("message"), Some(&json!("40%")), "the score as a percent");
        assert_eq!(badge(&run(40, &[])).get("color"), Some(&json!("red")), "under 60");
    }
}
