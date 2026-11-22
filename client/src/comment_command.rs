//! `krypton comment BEFORE.json AFTER.json [--line FILE] [--fail-if-worse]` writes the pull request scorecard (markdown) comparing two
//! summaries made by `krypton scan --summary`, and with `--line` the one line of the Krypton gate in the pull request's checks. The Action
//! puts the scorecard in the run summary and in one comment on the pull request, which it edits on every push instead of adding another;
//! the hidden [`MARKER`] on its first line is how it finds that comment. Only names and numbers are used; no source text is involved.
//!
//! With `--target N`, the files the pull request changed (a third summary, `--changed FILE`, made by `krypton scan --base`) must score N
//! or more: the target rule of the Krypton gate. The rest of the codebase is never judged. Without `--changed`, no source file changed.
//!
//! With `--unscored CODE` (the exit code of the step that stopped), it writes the comment and line for a push Krypton could not score:
//! what stopped it, that the code was not judged, and what to do. See [`unscored`].

use std::cmp::Ordering;
use std::fs;
use std::path::{Path, PathBuf};

use crate::runlog::{self, Run};
use crate::Failure;

const LISTED: usize = 10;
/// The hidden first line of the scorecard: the Action finds its own pull request comment by it, so it edits one comment instead of adding more.
pub const MARKER: &str = "<!-- krypton-gate -->";
/// The light lavender of the headline's scores, the middle of the bar's gradient, readable on GitHub's light and dark themes alike.
const LAVENDER: &str = "#9a7ed6";
/// A thin lavender gradient bar across the top of the comment. GitHub drops inline SVG, so it is a plain image on Krypton's site. GitHub
/// makes every image a link to its copy of the image unless it is already in a link, so it sits in a `#` link: clicking it goes nowhere.
const BAR: &str = r##"<a href="#"><img src="https://krypton.software/img/bar.svg" width="100%" height="4" alt=""></a>"##;
/// Krypton's mark before the name in the headline: a plain Unicode diamond, so it sits on the line with the words and draws the same in
/// GitHub, email notifications and terminals, with no image to load.
const CRYSTAL: &str = "◈";
const USAGE: &str = "usage: krypton comment BEFORE.json AFTER.json [--line FILE] [--fail-if-worse] [--target N [--changed CHANGED.json]]  |  krypton comment --unscored CODE [--on-server-error pass|fail] [--line FILE] [--state FILE]";

/// The scorecard's headline: the arrow, before, after and the change, all in lavender. GitHub has no text colour in markdown, but its math
/// does: `$\color{...}{...}$`. Each % is written `\\%`: GitHub's markdown turns it into `\%` before the maths reads it, where a bare % would
/// start a TeX comment and drop the rest.
///
/// The arrow is the ordinary character (`\text{▲}`), full size, where the maths' own triangle is tiny. The scores are bold sans-serif to
/// sit with the heading's own letters, and the sign is `{-}` so the maths does not space it like a subtraction.
fn coloured_headline(before: i64, after: i64) -> String {
    let (arrow, scores) = match after.cmp(&before) {
        Ordering::Greater => ("▲", format!(r"{before}\\%\ \rightarrow\ {after}\\%\ ({{+}}{})", after.saturating_sub(before))),
        Ordering::Less => ("▼", format!(r"{before}\\%\ \rightarrow\ {after}\\%\ ({{-}}{})", before.saturating_sub(after))),
        Ordering::Equal => ("●", format!(r"{after}\\%\ unchanged")),
    };
    format!(r"$\color{{{LAVENDER}}}{{\text{{{arrow}}}\ \boldsymbol{{\mathsf{{{scores}}}}}}}$")
}

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

fn worse_section(list: &[Worse]) -> Vec<String> {
    let noun = if list.len() == 1 { "function" } else { "functions" };
    let mut lines = vec![format!("**{} {noun} got worse**", list.len()), String::new()];
    lines.extend(list.iter().take(LISTED).map(line));
    if list.len() > LISTED {
        lines.push(format!("- …and {} more", list.len().saturating_sub(LISTED)));
    }
    lines
}

/// The scores table: purity and functions over the limit before and after, plus the pull request's own column when the target rule is on.
fn table(before: &Run, after: &Run, new_code: NewCode) -> Vec<String> {
    let (was, now) = (before.purity, after.purity);
    let (over_was, over_now) = (before.functions.len(), after.functions.len());
    let Some((this, target)) = new_code.column() else {
        return vec![
            "| | Before | After |".to_string(),
            "|---|---|---|".to_string(),
            format!("| Purity | {was}% | {now}% |"),
            format!("| Over the limit | {over_was} | {over_now} |"),
        ];
    };
    vec![
        "| | Before | After | This PR |".to_string(),
        "|---|---|---|---|".to_string(),
        format!("| Purity | {was}% | {now}% | {this} |"),
        format!("| Target | | | {target} |"),
        format!("| Over the limit | {over_was} | {over_now} | |"),
    ]
}

/// The target rule: how the files a pull request changed scored against the target, when the rule is on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NewCode {
    /// The rule is off.
    Unchecked,
    /// The rule is on, but the pull request changed no source file, so there is nothing to hold to the target.
    NoSourceChanged { target: i64 },
    /// The changed files' purity and the target they must reach.
    Scored { purity: i64, target: i64 },
}

impl NewCode {
    /// Whether this fails the gate: the changed files scored below the target.
    #[must_use]
    pub const fn fails(self) -> bool {
        matches!(self, Self::Scored { purity, target } if purity < target)
    }

    fn tail(self) -> String {
        match self {
            Self::Unchecked => String::new(),
            Self::NoSourceChanged { .. } => " · no source changed".to_string(),
            Self::Scored { purity, target } => format!(" · changed code {purity}% (target {target})"),
        }
    }

    /// The "This PR" column: the changed files' purity and the target with a tick or a cross; none when the rule is off.
    fn column(self) -> Option<(String, String)> {
        match self {
            Self::Unchecked => None,
            Self::NoSourceChanged { target } => Some(("no source changed".to_string(), target.to_string())),
            Self::Scored { purity, target } => Some((format!("{purity}%"), format!("{target} {}", if purity < target { "✗" } else { "✓" }))),
        }
    }
}

/// The scorecard: the Actions run summary of a pull request, and its one comment, opening with Krypton's lavender bar.
#[must_use]
pub fn markdown(before: &Run, after: &Run, new_code: NewCode) -> String {
    let list = worse_functions(before, after);
    let mut lines = vec![
        format!("### {CRYSTAL} Krypton · {}", coloured_headline(before.purity, after.purity)),
        String::new(),
        "**Repo Purity · Scores**".to_string(),
        String::new(),
    ];
    lines.extend(table(before, after, new_code));
    lines.push(String::new());
    if !list.is_empty() {
        lines.extend(worse_section(&list));
    } else if after.purity > before.purity {
        lines.push("Nice: this change made the code simpler.".to_string());
    } else {
        lines.push("No functions got worse.".to_string());
    }
    // the blank line after the bar ends its HTML block, or GitHub would read the headline as more HTML
    format!("{MARKER}\n{BAR}\n\n{}\n", lines.join("\n"))
}

/// The gate line with the target rule's result added, when the rule is on.
#[must_use]
pub fn gate_line(before: &Run, after: &Run, new_code: NewCode) -> String {
    status_line(before, after) + &new_code.tail()
}

/// The one line the "Krypton gate" status shows next to the other checks on a pull request: the purity before and after, the change, and how
/// many functions got worse. It is the whole signal on the pull request; Details opens the tree and the run summary has the scorecard.
#[must_use]
pub fn status_line(before: &Run, after: &Run) -> String {
    let tail = match worse_functions(before, after).len() {
        0 => String::new(),
        1 => " · 1 function worse".to_string(),
        many => format!(" · {many} functions worse"),
    };
    format!("Purity {}{tail}", headline(before.purity, after.purity))
}

/// Why a push could not be scored, from the exit code of the step that stopped, and whether it was the Krypton server's doing (only then
/// does `on-server-error: pass` let the gate through).
const fn unscored_reason(code: u8) -> (&'static str, bool) {
    match code {
        4 => ("a file could not be read or turned into a tree", false),
        5 => ("the runner ran out of memory or disk, or could not write a file", false),
        6 => ("the Krypton server could not be reached", true),
        7 => ("the Krypton server is busy", true),
        8 => ("the Krypton server refused the request", false),
        9 => ("the Krypton server failed", true),
        _ => ("Krypton stopped unexpectedly", false),
    }
}

/// The comment and the gate line for a push Krypton could not score, after the client had already tried again: what stopped it, that the
/// code was not judged, and what to do. `pass` is the workflow's `on-server-error: pass`.
#[must_use]
pub fn unscored(code: u8, pass: bool) -> (String, String) {
    let (why, server) = unscored_reason(code);
    let passing = server && pass;
    let advice = match (server, passing) {
        (true, true) => "The gate passes anyway, as this repository chose (`on-server-error: pass`). Re-run the job to score it.",
        (true, false) => {
            "This is not your code. Re-run the job to score it; to let pull requests through when this happens, set `on-server-error: \"pass\"` in the workflow."
        }
        _ => "Nothing about your code was judged. The job's log says what to fix; then re-run it.",
    };
    let scorecard = format!(
        "{MARKER}\n{BAR}\n\n### {CRYSTAL} Krypton · not scored\n\nKrypton could not score this push: {why} (exit {code}), even after trying again.\n\n{advice}\n"
    );
    let line = match (server, passing) {
        (true, true) => format!("Not scored: {why}. Passing, as set; re-run to score."),
        (true, false) => format!("Not scored: {why} (not your code). Re-run to score."),
        _ => format!("Not scored: {why} (exit {code}). See the job's log."),
    };
    (scorecard, line)
}

/// Whether the gate passes a push that could not be scored: only for the Krypton server's own trouble, and only when the workflow says so.
#[must_use]
pub const fn unscored_passes(code: u8, pass: bool) -> bool {
    unscored_reason(code).1 && pass
}

fn write(path: &Path, text: &str) -> Result<(), Failure> {
    fs::write(path, text).map_err(|error| Failure::Log(format!("{}: {error}", path.display())))
}

struct Options {
    files: Vec<PathBuf>,
    line: Option<PathBuf>,
    fail_if_worse: bool,
    target: Option<i64>,
    changed: Option<PathBuf>,
    /// The exit code of the step that stopped: write the "not scored" comment and line instead of a scorecard.
    unscored: Option<u8>,
    /// `--on-server-error pass`: the Krypton server's own trouble lets the gate through.
    pass_on_server_error: bool,
    /// With `--unscored`: where to write the gate's state, `success` (passing, as chosen) or `error`.
    state: Option<PathBuf>,
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let mut options =
        Options { files: Vec::new(), line: None, fail_if_worse: false, target: None, changed: None, unscored: None, pass_on_server_error: false, state: None };
    let mut words = args.iter();
    while let Some(word) = words.next() {
        match word.as_str() {
            "--fail-if-worse" => options.fail_if_worse = true,
            "--unscored" => {
                let text = words.next().ok_or_else(|| Failure::Usage(format!("--unscored needs the exit code\n{USAGE}")))?;
                options.unscored = Some(text.parse().map_err(|_bad| Failure::Usage(format!("--unscored is an exit code from 0 to 255\n{USAGE}")))?);
            }
            "--on-server-error" => match words.next().map(String::as_str) {
                Some("pass") => options.pass_on_server_error = true,
                Some("fail") => options.pass_on_server_error = false,
                _ => return Err(Failure::Usage(format!("--on-server-error is pass or fail\n{USAGE}"))),
            },
            "--target" => {
                let text = words.next().ok_or_else(|| Failure::Usage(format!("--target needs a number\n{USAGE}")))?;
                let bad = || Failure::Usage(format!("--target is a whole number from 0 to 100\n{USAGE}"));
                options.target = Some(crate::setup_command::parse_target(text).ok_or_else(bad)?);
            }
            "--state" => options.state = Some(PathBuf::from(words.next().ok_or_else(|| Failure::Usage(format!("--state needs a file\n{USAGE}")))?)),
            "--changed" => options.changed = Some(PathBuf::from(words.next().ok_or_else(|| Failure::Usage(format!("--changed needs a file\n{USAGE}")))?)),
            "--line" => options.line = Some(PathBuf::from(words.next().ok_or_else(|| Failure::Usage(format!("--line needs a file\n{USAGE}")))?)),
            flag if flag.starts_with("--") => return Err(Failure::Usage(format!("unknown option {flag}\n{USAGE}"))),
            path => options.files.push(PathBuf::from(path)),
        }
    }
    Ok(options)
}

pub fn comment(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    if let Some(code) = options.unscored {
        let (scorecard, line) = unscored(code, options.pass_on_server_error);
        print!("{scorecard}");
        if let Some(path) = &options.line {
            write(path, &line)?;
        }
        if let Some(path) = &options.state {
            write(path, if unscored_passes(code, options.pass_on_server_error) { "success" } else { "error" })?;
        }
        return Ok(());
    }
    let [before_path, after_path] = options.files.as_slice() else { return Err(Failure::Usage(format!("give the summary before and after\n{USAGE}"))) };
    let (before, after) = (runlog::read_summary(before_path)?, runlog::read_summary(after_path)?);
    let new_code = match (options.target, &options.changed) {
        (None, None) => NewCode::Unchecked,
        (None, Some(_)) => return Err(Failure::Usage(format!("--changed goes with --target\n{USAGE}"))),
        (Some(target), None) => NewCode::NoSourceChanged { target },
        (Some(target), Some(path)) => NewCode::Scored { purity: runlog::read_summary(path)?.purity, target },
    };
    let failed = (options.fail_if_worse && after.purity < before.purity) || new_code.fails();
    let scorecard = markdown(&before, &after, new_code);
    print!("{scorecard}");
    if let Some(path) = &options.line {
        write(path, &gate_line(&before, &after, new_code))?;
    }
    if failed {
        return Err(Failure::Flagged);
    }
    Ok(())
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
        let text = markdown(&run(71, &[("a.py::f", 3.0), ("a.py::g", 2.0)]), &run(74, &[("a.py::f", 2.0)]), NewCode::Unchecked);
        assert!(text.starts_with(&format!("<!-- krypton-gate -->\n{BAR}\n\n### {CRYSTAL} Krypton")), "the marker, the bar, then the headline");
        assert!(
            text.contains(r"Krypton · $\color{#9a7ed6}{\text{▲}\ \boldsymbol{\mathsf{71\\%\ \rightarrow\ 74\\%\ ({+}3)}}}$"),
            "arrow and scores in lavender"
        );
        assert!(text.contains("**Repo Purity · Scores**") && text.contains("| Over the limit | 2 | 1 |"), "the scores");
        assert!(!text.contains("This PR") && text.contains("Nice: this change made the code simpler."), "no target column, a kind word");
    }

    // With the target rule on, the table gets the pull request's own column: its score and the target with a tick or a cross.
    #[test]
    fn test_comment_target() {
        let text = markdown(&run(59, &[]), &run(60, &[]), NewCode::Scored { purity: 100, target: 70 });
        assert!(text.contains("| Purity | 59% | 60% | 100% |") && text.contains("| Target | | | 70 ✓ |"), "met");
        let short = markdown(&run(59, &[]), &run(60, &[]), NewCode::Scored { purity: 64, target: 70 });
        assert!(short.contains("| Target | | | 70 ✗ |"), "missed");
        let none = markdown(&run(59, &[]), &run(60, &[]), NewCode::NoSourceChanged { target: 70 });
        assert!(none.contains("| no source changed |"), "nothing to hold to the target");
    }

    // Only the functions the change made worse are listed, the biggest growth first, and a new one says it was under the limit.
    #[test]
    fn test_comment_worse() {
        let text = markdown(&run(74, &[("a.py::f", 3.0)]), &run(70, &[("a.py::f", 4.5), ("b.ts::g", 2.0)]), NewCode::Unchecked);
        assert!(text.contains(r"$\color{#9a7ed6}{\text{▼}\ \boldsymbol{\mathsf{74\\%\ \rightarrow\ 70\\%\ ({-}4)}}}$"), "down arrow");
        assert!(text.contains("**2 functions got worse**"), "the count");
        assert!(text.contains("- `b.ts · g` under the limit → 2×"), "a new over-limit function");
        assert!(text.contains("- `a.py · f` 3× → 4.5×"), "a function that grew");
    }

    // No change reads as unchanged, and a function that did not grow is not listed.
    #[test]
    fn test_comment_same() {
        let text = markdown(&run(74, &[("a.py::f", 3.0)]), &run(74, &[("a.py::f", 3.0)]), NewCode::Unchecked);
        assert!(text.contains(r"$\color{#9a7ed6}{\text{●}\ \boldsymbol{\mathsf{74\\%\ unchanged}}}$"), "dot headline");
        assert!(text.contains("No functions got worse."), "nothing worse");
    }

    // The status line is short and says before, after, the change, and how many functions got worse.
    #[test]
    fn test_status_line() {
        assert_eq!(status_line(&run(71, &[]), &run(74, &[])), "Purity ▲ 71% → 74% (+3)", "up, nothing worse");
        let worse = status_line(&run(74, &[("a.py::f", 3.0)]), &run(70, &[("a.py::f", 4.5), ("b.ts::g", 2.0)]));
        assert_eq!(worse, "Purity ▼ 74% → 70% (−4) · 2 functions worse", "down, with the count");
    }

    // A push Krypton could not score says so plainly: the server's trouble is "not your code", and passes only when the workflow chose that.
    #[test]
    fn test_unscored() {
        let (busy, line) = unscored(7, false);
        assert!(busy.starts_with(&format!("{MARKER}\n{BAR}\n\n### {CRYSTAL} Krypton · not scored")), "marker, bar, headline");
        assert!(busy.contains("the Krypton server is busy (exit 7)") && busy.contains("on-server-error"), "why, and the switch");
        assert_eq!(line, "Not scored: the Krypton server is busy (not your code). Re-run to score.", "the gate line");
        assert!(unscored(9, true).1.contains("Passing, as set") && unscored_passes(9, true), "passing when chosen");
        assert!(!unscored_passes(9, false) && !unscored_passes(4, true), "never for a file problem, and not unless chosen");
        assert!(unscored(4, true).1.len() <= 140 && unscored(6, false).1.len() <= 140, "fits GitHub's status line");
    }

    // The target rule judges only the changed files, says so on the gate line, and fails only below the target.
    #[test]
    fn test_target_rule() {
        let (base, head) = (run(56, &[]), run(57, &[]));
        let met = NewCode::Scored { purity: 72, target: 70 };
        let short = NewCode::Scored { purity: 64, target: 70 };
        assert_eq!(gate_line(&base, &head, met), "Purity ▲ 56% → 57% (+1) · changed code 72% (target 70)", "met");
        assert!(!met.fails() && short.fails(), "only below the target fails");
        assert!(!NewCode::NoSourceChanged { target: 70 }.fails(), "no source changed passes");
        assert_eq!(gate_line(&base, &head, NewCode::Unchecked), status_line(&base, &head), "off: the usual line");
    }
}
