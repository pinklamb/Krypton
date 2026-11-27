//! What `krypton scan` prints for a person at a terminal: Krypton's mark (◆) above the purity score, one line of what it means, and the
//! most tangled functions under their real names. The full report with every hint is one flag away (`--full`), and it is what a pipe, an
//! agent or CI gets anyway, so nothing that reads the output loses anything.

use serde_json::Value;

use crate::loader::{paint, BRIGHT, LAVENDER, MARK, MUTED};
use crate::runlog::Run;

/// How many functions the list shows before "and N more".
const LISTED: usize = 5;

fn times(value: f64) -> String {
    if value >= 100.0 {
        format!("{value:.0}×")
    } else {
        format!("{value:.1}×")
    }
}

/// A count with thousands separators: 1,150.
fn grouped(count: usize) -> String {
    let digits = count.to_string();
    let mut out = String::new();
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && digits.len().saturating_sub(index).is_multiple_of(3) {
            out.push(',');
        }
        out.push(digit);
    }
    out
}

fn plural(count: usize, one: &str, many: &str) -> String {
    format!("{} {}", grouped(count), if count == 1 { one } else { many })
}

/// The head of the report: Krypton's mark and name, the score, what it means, and what was scored.
fn head(run: &Run, result: &Value, usage: &Value, color: bool) -> [String; 4] {
    let over = run.functions.len();
    let flagged = result.get("flagged_file_count").and_then(Value::as_u64).unwrap_or(0);
    let files = usage.get("files").and_then(Value::as_u64).unwrap_or(0);
    let ms = usage.get("ms").and_then(Value::as_u64).unwrap_or(0);
    let verdict = if over == 0 {
        "No function is over the limit.".to_string()
    } else {
        format!("{} over the limit, in {}.", plural(over, "function", "functions"), plural(usize::try_from(flagged).unwrap_or(0), "file", "files"))
    };
    [
        format!("{}  {}{}", paint(MARK, LAVENDER, color), paint("Kr", BRIGHT, color), paint("ypton", LAVENDER, color)),
        format!("{}  {}", paint("Purity", MUTED, color), paint(&format!("{}%", run.purity), BRIGHT, color)),
        verdict,
        paint(&format!("{} scored in {ms} ms", plural(usize::try_from(files).unwrap_or(0), "file", "files")), MUTED, color),
    ]
}

/// `file::Class.method` as the function and its file, for two columns.
fn split_key(key: &str) -> (&str, &str) {
    key.split_once("::").map_or(("", key), |(file, function)| (file, function))
}

/// The most tangled functions, worst first, with how many times over the limit each is.
fn worst(run: &Run, color: bool) -> Vec<String> {
    let mut ranked: Vec<(&String, &f64)> = run.functions.iter().collect();
    ranked.sort_by(|left, right| right.1.total_cmp(left.1).then_with(|| left.0.cmp(right.0)));
    let width = ranked.iter().take(LISTED).map(|(key, _)| split_key(key).1.chars().count()).max().unwrap_or(0);
    let mut lines = vec![paint("  Most tangled", BRIGHT, color)];
    lines.extend(ranked.iter().take(LISTED).map(|(key, value)| {
        let (file, function) = split_key(key);
        // pad before painting: colour codes are invisible but would count toward the width
        format!("  {}  {function:<width$}  {}", paint(&format!("{:>7}", times(**value)), LAVENDER, color), paint(file, MUTED, color))
    }));
    if ranked.len() > LISTED {
        lines.push(paint(&format!("           …and {} more", grouped(ranked.len().saturating_sub(LISTED))), MUTED, color));
    }
    lines
}

/// What was scanned: the folder as it was typed (for the `--full` hint), and notes on what was not scored, shown under the count.
pub struct Target<'a> {
    pub folder: &'a str,
    pub notes: &'a [String],
}

/// The whole terminal view for one scan.
#[must_use]
pub fn compact(run: &Run, result: &Value, usage: &Value, target: &Target, color: bool) -> String {
    let mut lines: Vec<String> = head(run, result, usage, color).iter().map(|text| format!("  {text}").trim_end().to_string()).collect();
    lines.extend(target.notes.iter().map(|note| format!("  {}", paint(note, MUTED, color))));
    if !run.functions.is_empty() {
        lines.push(String::new());
        lines.extend(worst(run, color));
    }
    lines.push(String::new());
    lines.push(paint(&format!("  Hints and the full report: krypton scan {} --full", target.folder), MUTED, color));
    lines.join("\n") + "\n"
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use serde_json::json;

    use super::*;

    fn run(purity: i64, functions: &[(&str, f64)]) -> Run {
        Run {
            number: 0,
            time: 0,
            ticket: String::new(),
            purity,
            functions: functions.iter().map(|(k, v)| ((*k).to_string(), *v)).collect::<BTreeMap<_, _>>(),
            waived: BTreeMap::new(),
        }
    }

    fn sample() -> String {
        let scored = run(
            62,
            &[("garth/http.py::Client.configure", 11.8), ("garth/data/body_battery.py::BodyBatteryData.get", 46.2), ("garth/http.py::Client.request", 3.3)],
        );
        compact(&scored, &json!({ "flagged_file_count": 2 }), &json!({ "files": 145, "ms": 412 }), &Target { folder: ".", notes: &[] }, false)
    }

    // Krypton's mark and name head the score, the verdict and what was scored, as plain text with no bar.
    #[test]
    fn test_compact() {
        let text = sample();
        let lines: Vec<&str> = text.lines().collect();
        let at = |n: usize| lines.get(n).copied().unwrap_or_default();
        assert_eq!(at(0), "  ◆  Krypton", "the mark and the name");
        assert_eq!(at(1), "  Purity  62%", "the score");
        assert_eq!(at(2), "  3 functions over the limit, in 2 files.", "the verdict");
        assert_eq!(at(3), "  145 files scored in 412 ms", "what was scored");
        assert!(!text.contains('█') && !text.contains('░'), "no bar");
    }

    // The worst functions follow, worst first under their real names, then where the hints are.
    #[test]
    fn test_compact_worst() {
        let text = sample();
        let lines: Vec<&str> = text.lines().collect();
        let at = |n: usize| lines.get(n).copied().unwrap_or_default();
        assert!(at(5).contains("Most tangled"), "the list heading");
        assert!(
            at(6).contains("46.2×") && at(6).contains("BodyBatteryData.get") && at(6).ends_with("garth/data/body_battery.py"),
            "worst first, real name and file"
        );
        assert!(at(8).contains("3.3×") && at(8).contains("Client.request"), "then the rest in order");
        assert!(text.trim_end().ends_with("krypton scan . --full"), "where the hints are");
    }

    // Big counts get separators, and the times line up whatever colour does.
    #[test]
    fn test_grouped_and_aligned() {
        assert_eq!([grouped(7), grouped(1150), grouped(1_234_567)], ["7", "1,150", "1,234,567"], "thousands");
        let text = compact(&run(40, &[("a.py::f", 107.0), ("a.py::g", 9.5)]), &json!({}), &json!({}), &Target { folder: ".", notes: &[] }, true);
        let starts: Vec<usize> = text.lines().filter(|line| line.contains('×')).map(|line| line.find('×').unwrap_or(0)).collect();
        assert!(starts.len() == 2 && starts.windows(2).all(|pair| pair.first() == pair.last()), "the × signs line up even in colour");
    }

    // A clean codebase gets no list, and a long one is cut to five with a count.
    #[test]
    fn test_compact_lists() {
        let clean = compact(&run(100, &[]), &json!({}), &json!({ "files": 1, "ms": 5 }), &Target { folder: "src", notes: &[] }, false);
        assert!(clean.contains("No function is over the limit.") && !clean.contains("Most tangled"), "nothing to list");
        assert!(clean.contains("1 file scored"), "one file, singular");
        let many: Vec<(String, f64)> = (0..8).map(|n| (format!("a.py::f{n}"), f64::from(n) + 1.5)).collect();
        let borrowed: Vec<(&str, f64)> = many.iter().map(|(k, v)| (k.as_str(), *v)).collect();
        let long = compact(&run(40, &borrowed), &json!({ "flagged_file_count": 1 }), &json!({}), &Target { folder: ".", notes: &[] }, false);
        assert!(long.contains("…and 3 more") && long.contains("8.5×") && !long.contains("  2.5×"), "five shown, worst first");
    }

    // A note on what was not scored sits right under the count of files scored.
    #[test]
    fn test_compact_notes() {
        let notes = ["2 source files not scored; --full lists them and why".to_string()];
        let text = compact(&run(90, &[]), &json!({}), &json!({ "files": 3, "ms": 5 }), &Target { folder: ".", notes: &notes }, false);
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines.get(4).copied(), Some("  2 source files not scored; --full lists them and why"), "the note under the count");
    }
}
