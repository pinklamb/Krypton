//! `krypton setup [REPO] [--page [--theme cocoa|blossom]] [--mode manual|auto] [--target N] [--runs N] [--gate off|worse|target] [--no-map]`: set up Krypton for one
//! repository, once.
//!
//! 0. It logs you in with GitHub if you are not yet (`krypton login`): the server scores only for a logged-in user.
//! 1. It maps the whole codebase (an ordinary scan) and shows its purity today: the baseline.
//! 2. It asks for the **target**: the purity a ticket's code should reach. Set it above the baseline and every change the agent makes
//!    lifts the code it touches, so the rest of the codebase gets cleaner a ticket at a time. It suggests one from the baseline: a step
//!    above today's score, never below a target saved before, so running setup again as the codebase improves only ever raises it.
//! 3. It asks for **max tries**: how many runs the agent gets on a function before Krypton calls it stuck.
//! 4. It asks what happens when the agent is stuck: **ask me** (the agent builds a local review page for the current ticket, styled by
//!    `themes.md`) or **auto** (Krypton puts back the best run and the agent carries on).
//! 5. It asks whether to set up the **pull request gate** on GitHub: never worse (a pull request fails only if it lowers the score) or
//!    target (the files a pull request changes must reach the target), and writes `.github/workflows/krypton.yml` for it (see
//!    `workflow.rs`). That file is never overwritten, and nothing is committed: committing it turns the gate on.
//!
//! The questions are asked in the terminal, or, with `--page` (or by answering "browser" to the first question), on a one-time local
//! page where the settings are clicked instead (see `setup_page.rs`).
//!
//! The answers are kept in `.krypton/settings.json`, which git never sees; `krypton review` reads them. `krypton` with no command, in a
//! terminal, starts here, so `docker run -it` on the image is the whole setup.

use std::fs;
use std::io::{self, BufRead, IsTerminal, Write};
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::runlog;
use crate::workflow::{self, Gate, Written};
use crate::Failure;

const USAGE: &str =
    "usage: krypton setup [REPO] [--page [--listen HOST:PORT] [--theme cocoa|blossom]] [--mode manual|auto] [--target 0-100] [--runs 1-10] [--gate off|worse|target] [--no-map]";
const SETTINGS: &str = "settings.json";
const BASELINE: &str = "baseline.json";
/// Used when nobody answers a question (an empty line) and no flag was given.
pub const DEFAULT_TARGET: i64 = 70;
pub const DEFAULT_RUNS: usize = 3;
const MAX_RUNS: usize = 10;
/// How far above today's score the suggested target sits: far enough that every ticket lifts the code it touches.
const LIFT: i64 = 10;
/// The highest target Krypton suggests on its own; asking for a perfect score on every ticket is left to the person.
const SUGGEST_CAP: i64 = 95;

const fn round_up_to_five(score: i64) -> i64 {
    match score.rem_euclid(5) {
        0 => score,
        rest => score.saturating_add(5_i64.saturating_sub(rest)),
    }
}

/// The target to suggest: today's score plus a step, rounded up to a multiple of 5 and capped, but never below today's score and never
/// below a target saved before. The second rule is the ratchet: as tickets lift the codebase, running setup again raises the suggestion,
/// and nothing lowers it except the person typing a lower number.
#[must_use]
pub fn suggested_target(baseline: Option<i64>, kept: Option<i64>) -> i64 {
    let from_today = baseline.map_or(DEFAULT_TARGET, |score| round_up_to_five(score.saturating_add(LIFT)).min(SUGGEST_CAP).max(score));
    kept.map_or(from_today, |target| target.max(from_today))
}

/// What happens when a ticket has functions the agent could not fix.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// A person decides on a local review page.
    Manual,
    /// Krypton puts back the best run and the agent carries on.
    Auto,
}

impl Mode {
    pub const fn name(self) -> &'static str {
        match self {
            Self::Manual => "manual",
            Self::Auto => "auto",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        match text.trim().to_ascii_lowercase().as_str() {
            "manual" | "1" | "m" => Some(Self::Manual),
            "auto" | "2" | "a" => Some(Self::Auto),
            _ => None,
        }
    }
}

/// Everything `krypton setup` decides for a repository.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Settings {
    pub mode: Mode,
    /// The purity a ticket's code should reach.
    pub target: i64,
    /// Runs after the start before a function can count as stuck.
    pub max_runs: usize,
    /// The whole codebase's purity when setup ran, if it was mapped.
    pub baseline: Option<i64>,
    /// What fails a pull request on GitHub, if the gate was set up.
    pub gate: Gate,
}

impl Settings {
    /// What review uses before setup has been run.
    pub const DEFAULT: Self = Self { mode: Mode::Manual, target: DEFAULT_TARGET, max_runs: DEFAULT_RUNS, baseline: None, gate: Gate::Off };
}

fn settings_path(repo: &Path) -> PathBuf {
    runlog::folder(repo).join(SETTINGS)
}

fn bad_settings(path: &Path, what: &str) -> Failure {
    Failure::Log(format!("{} {what}; run `krypton setup` again", path.display()))
}

/// The settings saved by `krypton setup`, or `None` when setup has not been run here.
///
/// A settings file that cannot be read is an error, never a silent default.
pub fn saved(repo: &Path) -> Result<Option<Settings>, Failure> {
    let path = settings_path(repo);
    if !path.exists() {
        return Ok(None);
    }
    let text = fs::read_to_string(&path).map_err(|error| Failure::Read(path.display().to_string(), error))?;
    let value: Value = serde_json::from_str(&text).map_err(|error| bad_settings(&path, &format!("is not valid JSON: {error}")))?;
    let mode = value.get("review").and_then(Value::as_str).and_then(Mode::parse).ok_or_else(|| bad_settings(&path, "has no review mode (manual or auto)"))?;
    let target = value
        .get("target")
        .and_then(Value::as_i64)
        .filter(|target| (0..=100).contains(target))
        .ok_or_else(|| bad_settings(&path, "has no target from 0 to 100"))?;
    let max_runs = value
        .get("max_runs")
        .and_then(Value::as_u64)
        .and_then(|runs| usize::try_from(runs).ok())
        .filter(|runs| (1..=MAX_RUNS).contains(runs))
        .ok_or_else(|| bad_settings(&path, "has no max_runs from 1 to 10"))?;
    // Settings saved before the gate question existed have no gate: the gate was simply never set up there.
    let gate = value
        .get("gate")
        .and_then(Value::as_str)
        .map_or(Some(Gate::Off), Gate::parse)
        .ok_or_else(|| bad_settings(&path, "has a gate that is not off, worse or target"))?;
    Ok(Some(Settings { mode, target, max_runs, baseline: value.get("baseline").and_then(Value::as_i64), gate }))
}

fn save(repo: &Path, settings: &Settings) -> Result<PathBuf, Failure> {
    runlog::ensure_folder(repo)?;
    let path = settings_path(repo);
    let body = json!({ "review": settings.mode.name(), "target": settings.target, "max_runs": settings.max_runs, "baseline": settings.baseline, "gate": settings.gate.name() });
    let text = serde_json::to_string_pretty(&body).map_err(|error| Failure::Output(error.to_string()))?;
    fs::write(&path, text + "\n").map_err(|error| Failure::Log(format!("cannot write {}: {error}", path.display())))?;
    Ok(path)
}

/// Map the whole codebase with an ordinary scan and return its purity.
fn map_codebase(repo: &Path) -> Result<i64, Failure> {
    runlog::ensure_folder(repo)?;
    let summary = runlog::folder(repo).join(BASELINE);
    let args = [repo.display().to_string(), "--summary".to_string(), summary.display().to_string()];
    crate::scan_command::run(&args)?;
    Ok(runlog::read_summary(&summary)?.purity)
}

/// A starting `themes.md` for the review page, written only when the repository has none.
const STARTER_THEMES: &str = "# Krypton review page style

Your coding agent builds a small review page on your computer when it gets stuck, and it styles that page from this file.
Write it the way you would brief a designer. Anything you leave blank, the agent decides. This file only changes how the page looks,
never what it shows.

## The look

Calm and simple.

## Colors

- Background:
- Cards:
- Text:
- Accent, for buttons:

## Words on the buttons

- Approve: Approve best
- Send back: Send back
";

/// Write `themes.md` next to the code if there is none; never touch one that exists.
fn starter_themes(repo: &Path) -> Result<Option<PathBuf>, Failure> {
    let path = repo.join("themes.md");
    if path.exists() {
        return Ok(None);
    }
    fs::write(&path, STARTER_THEMES).map_err(|error| Failure::Log(format!("cannot write {}: {error}", path.display())))?;
    Ok(Some(path))
}

/// A starting `.kryptonignore`, written only when the repository has none: test files, matched the way the scoring server recognises them,
/// plus the `test/`, `spec/` and `__mocks__/` folders JavaScript projects use.
const STARTER_IGNORE: &str = "# Files Krypton does not score, in .gitignore's syntax. Whatever .gitignore skips is skipped too.
# Written by `krypton setup`. Edit it freely, and commit it so the pull request gate skips the same files.

# Tests check the code; they are not the code people change, so they do not count toward purity.
# Delete a line, or add one starting with ! (for example !tests/helpers.py), to score those files again.
test_*.py
*_test.py
conftest.py
tests/
test/
spec/
__tests__/
__mocks__/
*.test.*
*.spec.*
";

/// Write `.kryptonignore` next to the code if there is none, so tests are left out from the first scan on; never touch one that exists.
fn starter_ignore(repo: &Path) -> Result<Option<PathBuf>, Failure> {
    let path = repo.join(crate::scan_command::KRYPTON_IGNORE);
    if path.exists() {
        return Ok(None);
    }
    fs::write(&path, STARTER_IGNORE).map_err(|error| Failure::Log(format!("cannot write {}: {error}", path.display())))?;
    Ok(Some(path))
}

/// One terminal conversation: the questions are asked here and the answers read here.
struct Prompt<'a> {
    input: &'a mut dyn BufRead,
    output: &'a mut dyn Write,
}

impl Prompt<'_> {
    fn say(&mut self, text: &str) -> Result<(), Failure> {
        self.output.write_all(text.as_bytes()).and_then(|()| self.output.flush()).map_err(|error| Failure::Output(error.to_string()))
    }

    /// Ask until `parse` accepts the answer; an empty line takes `default`, the end of input is an error.
    fn ask<T>(&mut self, question: &str, default: T, parse: impl Fn(&str) -> Option<T>) -> Result<T, Failure> {
        self.say(question)?;
        loop {
            let mut line = String::new();
            let read = self.input.read_line(&mut line).map_err(|error| Failure::Read("standard input".to_string(), error))?;
            if read == 0 {
                return Err(Failure::Usage(format!("no answer was given\n{USAGE}")));
            }
            if line.trim().is_empty() {
                return Ok(default);
            }
            if let Some(answer) = parse(&line) {
                return Ok(answer);
            }
            self.say("That answer does not fit; try again: ")?;
        }
    }
}

pub fn parse_target(text: &str) -> Option<i64> {
    text.trim().trim_end_matches('%').parse().ok().filter(|target| (0..=100).contains(target))
}

pub fn parse_runs(text: &str) -> Option<usize> {
    text.trim().parse().ok().filter(|runs| (1..=MAX_RUNS).contains(runs))
}

fn target_question(baseline: Option<i64>, suggest: i64) -> String {
    let today = baseline.map_or_else(String::new, |score| {
        format!(
            "Your codebase scores {score}/100 today. Krypton suggests {suggest}, a step above: only new tickets are held to it, so new code \
             lifts the code it touches and old code is never blocked.\n"
        )
    });
    format!("\n{today}Target purity for each ticket's code, 0-100 [{suggest}]: ")
}

const WHERE_QUESTION: &str = "
Choose your settings:

  1) Here         Answer three questions in this terminal.
  2) In a browser Get a link to a page on this computer and click them yourself.

Choose 1 or 2 [1]: ";

/// Whether the person wants the settings page instead of the terminal questions.
fn parse_where(text: &str) -> Option<bool> {
    match text.trim().to_ascii_lowercase().as_str() {
        "1" | "here" | "terminal" => Some(false),
        "2" | "browser" | "page" => Some(true),
        _ => None,
    }
}

const RUNS_QUESTION: &str = "How many tries does the agent get on a function before it counts as stuck, 1-10 [3]: ";

const MODE_QUESTION: &str = "
When the agent is still stuck after those tries, what should happen?

  1) Ask me      Your agent builds a review page on this computer for the current ticket, styled by themes.md.
                 You approve the best try or send it back before anything goes to GitHub.
  2) Auto        Krypton puts back the best run on its own and the agent carries on.

Choose 1 or 2 [1]: ";

fn gate_question(target: i64) -> String {
    format!(
        "
Set up the pull request gate on GitHub? This writes .github/workflows/krypton.yml; commit it to turn the gate on.

  1) Not now
  2) Never worse  A pull request fails only if it lowers the purity score, so old code never blocks anyone.
  3) Target       A pull request fails if the files it changes score below your target ({target}).

Choose 1, 2 or 3 [2]: "
    )
}

/// Ask whatever the flags left open.
fn ask_all(prompt: &mut Prompt, given: &Options, baseline: Option<i64>, suggest: i64) -> Result<Settings, Failure> {
    let target = match given.target {
        Some(target) => target,
        None => prompt.ask(&target_question(baseline, suggest), suggest, parse_target)?,
    };
    let max_runs = match given.max_runs {
        Some(runs) => runs,
        None => prompt.ask(RUNS_QUESTION, DEFAULT_RUNS, parse_runs)?,
    };
    let mode = match given.mode {
        Some(mode) => mode,
        None => prompt.ask(MODE_QUESTION, Mode::Manual, Mode::parse)?,
    };
    let gate = match given.gate {
        Some(gate) => gate,
        None => prompt.ask(&gate_question(target), Gate::Worse, Gate::parse)?,
    };
    Ok(Settings { mode, target, max_runs, baseline, gate })
}

fn next_steps(settings: &Settings, ignore: Option<&Path>, themes: Option<&Path>, gate: &Written) -> String {
    let handled = if settings.mode == Mode::Auto { "automatically (best run kept)" } else { "by you, on a local review page" };
    let mut lines = vec![format!(
        "Saved: target {} purity per ticket, {} tries before a function is stuck, stuck functions handled {handled}, pull request gate: {}.",
        settings.target,
        settings.max_runs,
        gate_words(settings)
    )];
    if let Some(path) = ignore {
        lines.push(format!("Wrote {} so test files are not scored. Edit it to change what is skipped, and commit it.", path.display()));
    }
    if let Some(path) = themes {
        lines.push(format!("Wrote {} for you to style the review page.", path.display()));
    }
    match gate {
        Written::Wrote(path) => {
            lines.push(format!("Wrote {} (the pull request gate: {}). Commit it to turn the gate on.", path.display(), gate_words(settings)));
        }
        Written::Updated(path) => {
            lines.push(format!("Updated {} to the new gate ({}). Commit it so pull requests use the new rule.", path.display(), gate_words(settings)));
        }
        Written::Unchanged(path) => lines.push(format!("{} already has this gate.", path.display())),
        Written::Kept(path) => lines.push(format!("{} has your own edits (setup's line is gone), so it was left as it is.", path.display())),
        Written::StillOn(path) => lines.push(format!("{} from an earlier setup still gates pull requests; delete it to turn the gate off.", path.display())),
        Written::Off => {}
    }
    lines.push("Next: krypton scan . --log --ticket \"what you are fixing\"   (before and after each change)".to_string());
    lines.push(
        "Change any of this with krypton setup again, krypton setup --page, or: krypton setup --mode manual|auto --target N --runs N --gate off|worse|target"
            .to_string(),
    );
    lines.push("Setup finished.".to_string());
    lines.join("\n")
}

/// The gate as a person would say it.
fn gate_words(settings: &Settings) -> String {
    match settings.gate {
        Gate::Off => "none".to_string(),
        Gate::Worse => "never worse".to_string(),
        Gate::Target => format!("changed files must reach {}", settings.target),
    }
}

#[derive(Default)]
struct Options {
    repo: PathBuf,
    mode: Option<Mode>,
    target: Option<i64>,
    max_runs: Option<usize>,
    gate: Option<Gate>,
    no_map: bool,
    page: bool,
    listen: String,
    theme: crate::setup_page::Theme,
}

fn usage(message: &str) -> Failure {
    Failure::Usage(format!("{message}\n{USAGE}"))
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let listen = std::env::var("KRYPTON_LISTEN").unwrap_or_else(|_| crate::setup_page::LISTEN.to_string());
    let mut options = Options { repo: PathBuf::from("."), listen, ..Options::default() };
    let mut words = args.iter();
    while let Some(word) = words.next() {
        let mut value = |flag: &str| words.next().cloned().ok_or_else(|| usage(&format!("{flag} needs a value")));
        match word.as_str() {
            "--mode" => options.mode = Some(Mode::parse(&value("--mode")?).ok_or_else(|| usage("--mode is manual or auto"))?),
            "--target" => options.target = Some(parse_target(&value("--target")?).ok_or_else(|| usage("--target is a whole number from 0 to 100"))?),
            "--runs" => options.max_runs = Some(parse_runs(&value("--runs")?).ok_or_else(|| usage("--runs is a whole number from 1 to 10"))?),
            "--gate" => options.gate = Some(Gate::parse(&value("--gate")?).ok_or_else(|| usage("--gate is off, worse or target"))?),
            "--no-map" => options.no_map = true,
            "--page" => options.page = true,
            "--listen" => options.listen = value("--listen")?,
            "--theme" => options.theme = crate::setup_page::Theme::parse(&value("--theme")?).ok_or_else(|| usage("--theme is cocoa or blossom"))?,
            flag if flag.starts_with("--") => return Err(usage(&format!("unknown option {flag}"))),
            path => options.repo = PathBuf::from(path),
        }
    }
    Ok(options)
}

/// Whether `krypton` with no command should start setup: only when a person is at the keyboard.
pub fn interactive() -> bool {
    io::stdin().is_terminal()
}

/// The settings the flags give, with the suggested target and the defaults for anything left open. `gate` is the gate when no flag names
/// one: never worse when a person is choosing, off when nobody is (no workflow is written unasked).
fn from_flags(options: &Options, baseline: Option<i64>, suggest: i64, gate: Gate) -> Settings {
    Settings {
        mode: options.mode.unwrap_or(Mode::Manual),
        target: options.target.unwrap_or(suggest),
        max_runs: options.max_runs.unwrap_or(DEFAULT_RUNS),
        baseline,
        gate: options.gate.unwrap_or(gate),
    }
}

/// Ask in the terminal or on the settings page, whichever the person picks; flags already given are not asked again in the terminal.
fn choose(options: &Options, baseline: Option<i64>, suggest: i64) -> Result<Settings, Failure> {
    let mut prompt = Prompt { input: &mut io::stdin().lock(), output: &mut io::stdout().lock() };
    let everything_given = options.mode.is_some() && options.target.is_some() && options.max_runs.is_some() && options.gate.is_some();
    let page = options.page || (!everything_given && prompt.ask(WHERE_QUESTION, false, parse_where)?);
    if page {
        return crate::setup_page::choose(&options.listen, &from_flags(options, baseline, suggest, Gate::Worse), options.theme);
    }
    ask_all(&mut prompt, options, baseline, suggest)
}

/// The target saved by an earlier setup, for the ratchet. A damaged settings file is said out loud and then replaced, since rewriting it is
/// exactly what setup is for.
fn kept_target(repo: &Path) -> Option<i64> {
    match saved(repo) {
        Ok(settings) => settings.map(|settings| settings.target),
        Err(error) => {
            let why = match error {
                Failure::Log(message) => message,
                Failure::Read(path, error) => format!("cannot read {path}: {error}"),
                other => format!("{other:?}"),
            };
            eprintln!("{why} (setup will replace it)");
            None
        }
    }
}

pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let asking = interactive();
    if !asking && options.mode.is_none() && !options.page {
        return Err(usage("no terminal to ask in: pass --mode manual or --mode auto, or --page for the settings page (or run the image with docker run -it)"));
    }
    if asking {
        crate::loader::splash();
        if !options.no_map {
            crate::login_command::ensure(&crate::http::Server::from_flag(None))?;
        }
        eprintln!("Mapping your codebase ...");
    }
    // before the map, so the baseline already leaves the tests out
    let ignore = starter_ignore(&options.repo)?;
    let baseline = if options.no_map { None } else { Some(map_codebase(&options.repo)?) };
    let suggest = suggested_target(baseline, kept_target(&options.repo));
    let settings = if asking || options.page { choose(&options, baseline, suggest)? } else { from_flags(&options, baseline, suggest, Gate::Off) };
    save(&options.repo, &settings)?;
    let themes = if settings.mode == Mode::Manual { starter_themes(&options.repo)? } else { None };
    let gate = workflow::write(&options.repo, settings.gate, settings.target)?;
    println!("{}", next_steps(&settings, ignore.as_deref(), themes.as_deref(), &gate));
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::{ask_all, parse_where, save, saved, starter_ignore, starter_themes, suggested_target, Gate, Mode, Options, Prompt, Settings};

    fn answers(typed: &str) -> Option<Settings> {
        let mut input = Cursor::new(typed.as_bytes().to_vec());
        let mut output = Vec::new();
        ask_all(&mut Prompt { input: &mut input, output: &mut output }, &Options::default(), Some(28), suggested_target(Some(28), None)).ok()
    }

    // Enter four times takes the defaults: the target suggested from today's score, and the never-worse gate.
    #[test]
    fn test_defaults() {
        assert_eq!(
            answers("\n\n\n\n"),
            Some(Settings { mode: Mode::Manual, target: 40, max_runs: 3, baseline: Some(28), gate: Gate::Worse }),
            "40 (28 + 10, rounded up), 3, ask me, never worse"
        );
    }

    // The suggestion is a step above today, capped, never below today, and it only ratchets up.
    #[test]
    fn test_suggested_target() {
        assert_eq!(suggested_target(Some(56), None), 70, "56 + 10 rounds up to 70");
        assert_eq!(suggested_target(Some(60), None), 70, "a multiple of 5 stays");
        assert_eq!(suggested_target(Some(90), None), 95, "capped at 95");
        assert_eq!(suggested_target(Some(98), None), 98, "never below today");
        assert_eq!(suggested_target(None, None), 70, "no map: the usual 70");
        assert_eq!(suggested_target(Some(56), Some(80)), 80, "a higher saved target is kept");
        assert_eq!(suggested_target(Some(78), Some(70)), 90, "a better codebase raises it");
    }

    // A wrong answer is asked again.
    #[test]
    fn test_asks_again() {
        assert_eq!(
            answers("150\n75%\n0\n5\nmaybe\n2\nsure\n3\n").map(|s| (s.target, s.max_runs, s.mode, s.gate)),
            Some((75, 5, Mode::Auto, Gate::Target)),
            "the fitting answers count"
        );
    }

    // "Here" or "browser" picks where the settings are chosen.
    #[test]
    fn test_parse_where() {
        assert_eq!([parse_where("1"), parse_where("browser"), parse_where("x")], [Some(false), Some(true), None], "1 or 2, here or browser");
    }

    // No answer at all is an error, not a guess.
    #[test]
    fn test_no_answer() {
        assert_eq!(answers(""), None, "end of input is refused");
    }

    // The saved settings are read back, and an existing themes.md is never overwritten.
    #[test]
    fn test_saved() {
        let repo = std::env::temp_dir().join(format!("krypton-setup-{}", std::process::id()));
        std::fs::create_dir_all(&repo).unwrap();
        let wanted = Settings { mode: Mode::Auto, target: 72, max_runs: 4, baseline: Some(28), gate: Gate::Target };
        save(&repo, &wanted).unwrap();
        assert_eq!(saved(&repo).unwrap(), Some(wanted), "what was saved comes back");
        std::fs::write(repo.join("themes.md"), "mine").unwrap();
        assert!(starter_themes(&repo).unwrap().is_none(), "an existing themes.md is kept");
        assert_eq!(std::fs::read_to_string(repo.join("themes.md")).unwrap(), "mine", "and left untouched");
        std::fs::remove_dir_all(&repo).unwrap();
    }

    // The starter .kryptonignore leaves every kind of test file out of the scan, keeps the code, and an existing one is never touched.
    #[test]
    fn test_starter_ignore() {
        let repo = std::env::temp_dir().join(format!("krypton-starter-ignore-{}", std::process::id()));
        let tests = [
            "tests/test_api.py",
            "app/test_io.py",
            "app/io_test.py",
            "conftest.py",
            "web/__tests__/a.js",
            "web/a.test.ts",
            "web/b.spec.js",
            "test/c.js",
            "spec/d.ts",
            "web/__mocks__/e.js",
        ];
        for name in ["app/main.py", "web/app.ts", "app/contest.py"].iter().chain(tests.iter()) {
            let path = repo.join(name);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, "x = 1\n").unwrap();
        }
        assert!(starter_ignore(&repo).unwrap().is_some(), "written when there is none");
        let found = crate::scan_command::source_files(&repo).unwrap();
        assert_eq!(found, ["app/contest.py", "app/main.py", "web/app.ts"], "only the code is scanned");
        assert!(starter_ignore(&repo).unwrap().is_none(), "an existing one is kept");
        std::fs::remove_dir_all(&repo).unwrap();
    }
}
