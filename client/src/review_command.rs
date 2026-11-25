//! `krypton review [REPO] [--ticket TEXT] [--json] [--auto|--manual]`: reads the run log and says what a person should look at. A function is
//! stuck when, after the start and 3 runs, it is still over the limit and no run got it more than 10% better than where it started. The answer
//! is the data a coding agent turns into the review page for the current ticket (see `agent/review-brief.md`). In auto mode (chosen with
//! `krypton setup`, or `--auto`) nobody is asked: when something is stuck, the best run is put back here and the answer says so.

use std::path::PathBuf;

use serde_json::{json, Value};

use crate::attempts::{restore, Request};
use crate::runlog::{self, Run};
use crate::setup_command::{saved, Mode, Settings};
use crate::Failure;

/// A run must get a function at least this much better than the start to count as an improvement.
const IMPROVEMENT: f64 = 0.10;
const USAGE: &str = "usage: krypton review [REPO] [--ticket TEXT] [--json] [--auto|--manual]";

#[derive(Debug, PartialEq)]
struct Stuck {
    key: String,
    ratings: Vec<Option<f64>>,
    best_run: usize,
}

fn series(runs: &[&Run], key: &str) -> Vec<Option<f64>> {
    runs.iter().map(|run| run.functions.get(key).copied()).collect()
}

/// Still over the limit in every run after the start, with at least `tries` runs, and never 10% better than the start.
fn is_stuck(series: &[Option<f64>], tries: usize) -> bool {
    let (Some(Some(start)), Some(after)) = (series.first(), series.get(1..)) else { return false };
    after.len() >= tries && after.iter().all(|value| value.is_some_and(|times| times > start * (1.0 - IMPROVEMENT)))
}

/// The run with the lowest score (the earliest on a tie).
fn best_run(series: &[Option<f64>]) -> usize {
    let scored = series.iter().enumerate().filter_map(|(run, value)| value.map(|times| (run, times)));
    scored
        .fold(None, |best: Option<(usize, f64)>, (run, times)| if best.is_some_and(|(_, lowest)| lowest <= times) { best } else { Some((run, times)) })
        .map_or(0, |(run, _)| run)
}

fn start_of(stuck: &Stuck) -> f64 {
    stuck.ratings.first().copied().flatten().unwrap_or(0.0)
}

fn stuck_functions(runs: &[&Run], tries: usize) -> Vec<Stuck> {
    let Some(first) = runs.first() else { return Vec::new() };
    let all = first.functions.keys().map(|key| (key, series(runs, key)));
    let mut found: Vec<Stuck> =
        all.filter(|(_, ratings)| is_stuck(ratings, tries)).map(|(key, ratings)| Stuck { best_run: best_run(&ratings), key: key.clone(), ratings }).collect();
    found.sort_by(|left, right| start_of(right).total_cmp(&start_of(left)));
    found
}

/// How many functions that were over the limit at the start are now under it or at least 10% better.
fn improved(runs: &[&Run]) -> usize {
    let (Some(first), Some(last)) = (runs.first(), runs.last()) else { return 0 };
    let better = |key: &String, start: &f64| last.functions.get(key).is_none_or(|now| *now <= start * (1.0 - IMPROVEMENT));
    first.functions.iter().filter(|(key, start)| better(key, start)).count()
}

/// The run with the highest purity (the earliest on a tie): the one "approve best" puts back.
fn restore_run(runs: &[&Run]) -> usize {
    let best = runs.iter().fold(None, |best: Option<&&Run>, run| if best.is_some_and(|held| held.purity >= run.purity) { best } else { Some(run) });
    best.map_or(0, |run| run.number)
}

fn stuck_json(stuck: &Stuck) -> Value {
    let (file, function) = stuck.key.split_once("::").unwrap_or((stuck.key.as_str(), ""));
    let best = stuck.ratings.get(stuck.best_run).copied().flatten();
    json!({ "file": file, "function": function, "ratings": stuck.ratings, "best_run": stuck.best_run, "best": best })
}

fn review_json(ticket: &str, runs: &[&Run], settings: &Settings) -> Value {
    let (best, tries) = (restore_run(runs), settings.max_runs);
    let stuck = stuck_functions(runs, tries);
    let now = runs.last().map(|run| run.purity);
    let purity_of = |number: usize| runs.iter().find(|run| run.number == number).map(|run| run.purity);
    let note = (runs.len() <= tries).then(|| format!("{} runs are logged; stuck functions need the start and {tries} runs after it", runs.len()));
    json!({
        "ticket": ticket,
        "runs": runs.iter().map(|run| json!({ "run": run.number, "purity": run.purity })).collect::<Vec<_>>(),
        "purity": { "start": runs.first().map(|run| run.purity), "now": now, "if_approved": purity_of(best) },
        "target": settings.target,
        "max_runs": tries,
        "meets_target": now.is_some_and(|score| score >= settings.target),
        "restore_run": best,
        "needs_review": !stuck.is_empty(),
        "improved": improved(runs),
        "stuck": stuck.iter().map(stuck_json).collect::<Vec<_>>(),
        "note": note,
    })
}

fn text_of(value: &Value, key: &str) -> String {
    value.get(key).map_or_else(|| "?".to_string(), |found| found.as_str().map_or_else(|| found.to_string(), ToString::to_string))
}

fn times(value: &Value) -> String {
    value.as_f64().map_or_else(|| "under".to_string(), |number| format!("{number}×"))
}

fn print_stuck(item: &Value) {
    let ratings: Vec<String> = item.get("ratings").and_then(Value::as_array).into_iter().flatten().map(times).collect();
    println!("  {} · {}: {} (best: run {})", text_of(item, "file"), text_of(item, "function"), ratings.join(" → "), text_of(item, "best_run"));
}

fn print_text(review: &Value) {
    println!("Ticket: {}", text_of(review, "ticket"));
    let purity = review.get("purity").unwrap_or(&Value::Null);
    println!(
        "Purity: start {}, now {}, {} if run {} is put back",
        text_of(purity, "start"),
        text_of(purity, "now"),
        text_of(purity, "if_approved"),
        text_of(review, "restore_run")
    );
    let met = if review.get("meets_target") == Some(&Value::Bool(true)) { "met" } else { "not met yet" };
    println!("Target: {} ({met})", text_of(review, "target"));
    let stuck = review.get("stuck").and_then(Value::as_array).map_or(&[][..], Vec::as_slice);
    println!("Stuck after {} runs: {}", text_of(review, "max_runs"), stuck.len());
    stuck.iter().for_each(print_stuck);
    println!("Improved by more than 10%: {}", text_of(review, "improved"));
    let order = review.get("fix_order").and_then(Value::as_array).map_or(&[][..], Vec::as_slice);
    if !order.is_empty() {
        println!("Fix in this order, inner helpers before the functions that call them:");
        for (number, item) in order.iter().enumerate() {
            println!("  {}. {} · {}", number.saturating_add(1), text_of(item, "file"), text_of(item, "function"));
        }
    }
    if let Some(note) = review.get("note").and_then(Value::as_str) {
        println!("Note: {note}");
    }
    if let Some(done) = review.get("auto_approved").filter(|value| !value.is_null()) {
        println!("Auto-approved: put back run {} ({} files)", text_of(done, "run"), done.get("files").and_then(Value::as_array).map_or(0, Vec::len));
    } else if review.get("needs_review") == Some(&Value::Bool(true)) {
        println!("Mode: manual. Your agent builds the review page for this ticket from themes.md; you approve the best run or send it back.");
    }
}

struct Options {
    repo: PathBuf,
    ticket: Option<String>,
    json: bool,
    mode: Option<Mode>,
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let mut options = Options { repo: PathBuf::from("."), ticket: None, json: false, mode: None };
    let mut words = args.iter();
    while let Some(word) = words.next() {
        match word.as_str() {
            "--json" => options.json = true,
            "--auto" => options.mode = Some(Mode::Auto),
            "--manual" => options.mode = Some(Mode::Manual),
            "--ticket" => options.ticket = Some(words.next().cloned().ok_or_else(|| Failure::Usage(format!("--ticket needs a value\n{USAGE}")))?),
            flag if flag.starts_with("--") => return Err(Failure::Usage(format!("unknown option {flag}\n{USAGE}"))),
            path => options.repo = PathBuf::from(path),
        }
    }
    Ok(options)
}

/// Every function over the limit in the latest run, in the order to untangle them (helpers first, see `Graph::helpers_first`), and the
/// stuck list sorted the same way.
fn add_fix_order(review: &mut Value, latest: &Run, repo: &std::path::Path) -> Result<(), Failure> {
    let keys: Vec<String> = latest.functions.keys().cloned().collect();
    let order = crate::impact_command::load_graph(repo)?.helpers_first(&keys);
    let place = |item: &Value| order.iter().position(|key| *key == format!("{}::{}", text_of(item, "file"), text_of(item, "function"))).unwrap_or(usize::MAX);
    if let Some(stuck) = review.get_mut("stuck").and_then(Value::as_array_mut) {
        stuck.sort_by_key(place);
    }
    let listed: Vec<Value> = order
        .iter()
        .map(|key| key.split_once("::").map_or_else(|| json!({ "file": key }), |(file, function)| json!({ "file": file, "function": function })))
        .collect();
    if let Some(fields) = review.as_object_mut() {
        fields.insert("fix_order".to_string(), Value::from(listed));
    }
    Ok(())
}

pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let all = runlog::read_all(&options.repo)?;
    let none = || Failure::Log("no runs are logged yet: run `krypton scan . --log --ticket \"what you are fixing\"` before and after each change".to_string());
    let ticket = options.ticket.clone().or_else(|| runlog::latest_ticket(&all).map(str::to_string)).ok_or_else(none)?;
    let mine = runlog::for_ticket(&all, &ticket);
    if mine.is_empty() {
        return Err(Failure::Log(format!("no runs are logged for the ticket {ticket:?}")));
    }
    let mut settings = saved(&options.repo)?.unwrap_or(Settings::DEFAULT);
    if let Some(mode) = options.mode {
        settings.mode = mode;
    }
    let mode = settings.mode;
    let mut review = review_json(&ticket, &mine, &settings);
    if let Some(latest) = mine.last() {
        add_fix_order(&mut review, latest, &options.repo)?;
    }
    let approved = auto_approve(&options.repo, &ticket, &mine, &review, mode)?;
    if let Some(fields) = review.as_object_mut() {
        fields.insert("mode".to_string(), Value::from(mode.name()));
        fields.insert("auto_approved".to_string(), approved);
    }
    if options.json {
        println!("{}", serde_json::to_string_pretty(&review).map_err(|error| Failure::Output(error.to_string()))?);
    } else {
        print_text(&review);
    }
    Ok(())
}

/// In auto mode, with something stuck: put back the best run now, as "approve best" would. Otherwise nothing happens (`null`).
fn auto_approve(repo: &std::path::Path, ticket: &str, runs: &[&Run], review: &Value, mode: Mode) -> Result<Value, Failure> {
    if mode != Mode::Auto || review.get("needs_review") != Some(&Value::Bool(true)) {
        return Ok(Value::Null);
    }
    let target = review.get("restore_run").and_then(Value::as_u64).and_then(|run| usize::try_from(run).ok()).unwrap_or(0);
    let outcome = restore(&Request { repo, ticket, runs, target, dry_run: false, force: false })?;
    Ok(json!({ "run": target, "files": outcome.files, "already": outcome.already }))
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;

    /// The score of the function (none when under the limit) and the purity of one run.
    type Step = (Option<f64>, i64);

    fn runs(values: &[Step]) -> Vec<Run> {
        let make = |(number, (times, purity)): (usize, &Step)| Run {
            number,
            time: 0,
            ticket: "t".to_string(),
            purity: *purity,
            functions: times.map(|value| ("a.py::f".to_string(), value)).into_iter().collect::<BTreeMap<_, _>>(),
            waived: BTreeMap::new(),
        };
        values.iter().enumerate().map(make).collect()
    }

    fn stuck_of(values: &[Step]) -> Vec<Stuck> {
        let owned = runs(values);
        let all: Vec<&Run> = owned.iter().collect();
        stuck_functions(&all, 3)
    }

    // Three runs that barely help leave a function stuck, and the best of them is named.
    #[test]
    fn test_stuck_flat() {
        let found = stuck_of(&[(Some(46.0), 70), (Some(45.0), 71), (Some(43.0), 72), (Some(44.0), 71)]);
        assert_eq!(found.len(), 1, "one stuck function");
        assert_eq!(found.first().map(|stuck| stuck.best_run), Some(2), "run 2 scored lowest");
    }

    // A run that gets it 10% better, or under the limit, clears it.
    #[test]
    fn test_not_stuck() {
        assert!(stuck_of(&[(Some(10.0), 70), (Some(9.5), 70), (Some(8.0), 71), (Some(8.5), 71)]).is_empty(), "20% better at run 2");
        assert!(stuck_of(&[(Some(10.0), 70), (Some(9.5), 70), (None, 71), (Some(9.5), 71)]).is_empty(), "under the limit once means it is not stuck");
    }

    // Fewer than three runs after the start is too early to say.
    #[test]
    fn test_too_early() {
        assert!(stuck_of(&[(Some(46.0), 70), (Some(46.0), 70), (Some(46.0), 70)]).is_empty(), "only two runs after the start");
    }

    // "Approve best" puts back the run with the highest purity, the earliest on a tie.
    #[test]
    fn test_restore_run() {
        let owned = runs(&[(None, 70), (None, 74), (None, 74), (None, 72)]);
        let all: Vec<&Run> = owned.iter().collect();
        assert_eq!(restore_run(&all), 1, "run 1 reached 74 first");
    }

    // The data for the page carries the ticket, the purity if approved and each stuck function's scores.
    #[test]
    fn test_review_data() {
        let owned = runs(&[(Some(46.0), 70), (Some(45.0), 71), (Some(43.0), 74), (Some(44.0), 72)]);
        let all: Vec<&Run> = owned.iter().collect();
        let review = review_json("fix rounding", &all, &Settings::DEFAULT);
        assert_eq!(review.pointer("/purity/if_approved"), Some(&json!(74)), "the best run's purity");
        assert_eq!(review.get("restore_run"), Some(&json!(2)), "the run to put back");
        assert_eq!(review.pointer("/stuck/0/function"), Some(&json!("f")), "the function name");
        assert_eq!(review.pointer("/stuck/0/best"), Some(&json!(43.0)), "its best score");
        assert_eq!(review.get("meets_target"), Some(&json!(true)), "72 meets the default target of 70");
    }

    // More tries in the settings means a function is not called stuck as early.
    #[test]
    fn test_more_tries() {
        let steps = [(Some(46.0), 70), (Some(45.0), 71), (Some(43.0), 72), (Some(44.0), 71)];
        let owned = runs(&steps);
        let all: Vec<&Run> = owned.iter().collect();
        assert_eq!(stuck_functions(&all, 3).len(), 1, "stuck after 3 tries");
        assert!(stuck_functions(&all, 5).is_empty(), "not yet with 5 tries");
    }
}
