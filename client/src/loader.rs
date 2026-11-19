//! The terminal loader: one arrow slowly draws a lavender crystal, and the crystal closing means the run is done.
//!
//! It is built so that it cannot get in the way of the real output:
//!
//! * it writes to standard error only, so `--json` and pipes on standard output are untouched;
//! * it draws nothing unless standard error is a real terminal (not in CI, pipes, `TERM=dumb`, on Windows consoles, or with `--no-loader`);
//! * it draws nothing for the first 0.3 s, so a quick run never flickers;
//! * it erases itself before the report prints and always gives the cursor back, even when the run fails;
//! * it never slows or changes the scoring: a side thread only reads two counters and the stage name the run updates.
//!
//! Beside the crystal it names the stage the run is in (reading the files, building the trees with a count, scoring on the server).

use std::env;
use std::io::{self, IsTerminal, Write};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, PoisonError};
use std::thread::{self, JoinHandle};
use std::time::Duration;

pub const PRISM: [&str; 6] = ["   /\\   ", "  /  \\  ", " /    \\ ", " \\    / ", "  \\  /  ", "   \\/   "];
/// Down the left edge, along the bottom, up the right edge, back to the top: 12 cells, one arrow step each.
const PATH: [(usize, usize); 12] = [(0, 3), (1, 2), (2, 1), (3, 1), (4, 2), (5, 3), (5, 4), (4, 5), (3, 6), (2, 6), (1, 5), (0, 4)];
const STEPS: usize = PATH.len();
pub type Rgb = (u8, u8, u8);
/// The outline, and the arrow head and the finishing flash.
pub const LAVENDER: Rgb = (199, 181, 236);
pub const BRIGHT: Rgb = (240, 234, 255);
const HIDE_CURSOR: &str = "\x1b[?25l";
const SHOW_CURSOR: &str = "\x1b[?25h";
const SHOW_AFTER: Duration = Duration::from_millis(300);
const TICK: Duration = Duration::from_millis(150);
const FLASH: Duration = Duration::from_millis(120);
/// The stage text, dimmer than the crystal.
pub const MUTED: Rgb = (168, 156, 144);

/// The arrow points the way it travels: down, then along the bottom, then up.
const fn arrow_head(index: usize) -> char {
    if index < 6 {
        'v'
    } else if index == 6 {
        '>'
    } else {
        '^'
    }
}

/// `text` in a 24-bit colour, or unchanged when colour is off (`NO_COLOR`).
pub fn paint(text: &str, rgb: Rgb, color: bool) -> String {
    if color {
        format!("\x1b[38;2;{};{};{}m{text}\x1b[0m", rgb.0, rgb.1, rgb.2)
    } else {
        text.to_string()
    }
}

fn outline_char(cell: (usize, usize)) -> String {
    PRISM.get(cell.0).and_then(|line| line.chars().nth(cell.1)).map_or_else(|| " ".to_string(), String::from)
}

fn place(rows: &mut [Vec<String>], cell: (usize, usize), text: String) {
    if let Some(slot) = rows.get_mut(cell.0).and_then(|row| row.get_mut(cell.1)) {
        *slot = text;
    }
}

/// The crystal's lines with the first `drawn` outline cells in place and, if `head`, the arrow on the next cell.
fn frame(drawn: usize, head: bool, tint: Rgb, color: bool) -> Vec<String> {
    let mut rows: Vec<Vec<String>> = PRISM.iter().map(|line| line.chars().map(|_| " ".to_string()).collect()).collect();
    for &cell in PATH.iter().take(drawn) {
        place(&mut rows, cell, paint(&outline_char(cell), tint, color));
    }
    if let (true, Some(&cell)) = (head && drawn < STEPS, PATH.get(drawn)) {
        place(&mut rows, cell, paint(&arrow_head(drawn).to_string(), BRIGHT, color));
    }
    rows.into_iter().map(|row| format!("  {}", row.concat())).collect()
}

/// The crystal's lines with the stage written beside its middle, and while files are being counted, how many are done.
fn with_stage(mut lines: Vec<String>, stage: &str, done: usize, total: usize, color: bool) -> Vec<String> {
    if stage.is_empty() {
        return lines;
    }
    if let Some(row) = lines.get_mut(2) {
        row.push_str("   ");
        row.push_str(&paint(stage, BRIGHT, color));
    }
    if let (Some(row), true) = (lines.get_mut(3), total > 1 && done < total) {
        row.push_str("   ");
        row.push_str(&paint(&format!("{done} of {total} files"), MUTED, color));
    }
    lines
}

/// How far the arrow should be for `done` of `total` files. It never reaches the last step: only finishing the run does.
fn target_step(done: usize, total: usize) -> usize {
    STEPS.saturating_mul(done).checked_div(total).unwrap_or(0).min(STEPS.saturating_sub(1))
}

/// The crystal drawn once, start to finish, with the name under it: the greeting `krypton setup` opens with. Nothing is drawn where the
/// loader would not draw (see [`wanted`]).
pub fn splash() {
    if !wanted(&Environment::read(), false) {
        return;
    }
    let color = env::var_os("NO_COLOR").is_none();
    let mut out = io::stderr().lock();
    let mut lines = 0;
    for step in 0..=STEPS {
        let rows = frame(step, step < STEPS, LAVENDER, color);
        let rewind = if lines == 0 { String::new() } else { format!("[{lines}F") };
        if out
            .write_all(
                format!(
                    "{HIDE_CURSOR}{rewind}{}
",
                    rows.join(
                        "
"
                    )
                )
                .as_bytes(),
            )
            .and_then(|()| out.flush())
            .is_err()
        {
            return;
        }
        lines = rows.len();
        thread::sleep(Duration::from_millis(70));
    }
    let name = format!(
        "  {}{}

",
        paint("Kr", BRIGHT, color),
        paint("ypton", LAVENDER, color)
    );
    // a closed terminal at the very end leaves nothing to clean up
    let _done = out.write_all(format!("{name}{SHOW_CURSOR}").as_bytes()).and_then(|()| out.flush()).is_ok();
}

/// What the loader needs to know about where it is running.
pub struct Environment {
    pub is_terminal: bool,
    pub term: Option<String>,
    pub in_ci: bool,
    pub opted_out: bool,
}

impl Environment {
    /// Read it from the process: standard error, `TERM`, `CI` and `KRYPTON_NO_LOADER`.
    #[must_use]
    pub fn read() -> Self {
        Self {
            is_terminal: io::stderr().is_terminal(),
            term: env::var("TERM").ok(),
            in_ci: env::var_os("CI").is_some(),
            opted_out: env::var_os("KRYPTON_NO_LOADER").is_some(),
        }
    }
}

/// True only for a real, capable terminal and a person who did not switch the loader off. Nothing is drawn anywhere else.
#[must_use]
pub fn wanted(environment: &Environment, switched_off: bool) -> bool {
    let capable = environment.is_terminal && environment.term.as_deref() != Some("dumb") && !cfg!(windows);
    capable && !environment.in_ci && !environment.opted_out && !switched_off
}

/// Starts the loader when this is a terminal that should show it.
#[must_use]
pub fn start_if_wanted(switched_off: bool) -> Option<Loader> {
    wanted(&Environment::read(), switched_off).then(|| Loader::start(env::var_os("NO_COLOR").is_none()))
}

struct Shared {
    stage: Mutex<String>,
    done: AtomicUsize,
    total: AtomicUsize,
    lines: AtomicUsize,
    hidden: AtomicBool,
    stopped: Mutex<bool>,
    wake: Condvar,
    color: bool,
    out: Mutex<Box<dyn Write + Send>>,
}

impl Shared {
    /// Wait up to `span`; true when the loader was stopped meanwhile.
    fn wait(&self, span: Duration) -> bool {
        let guard = self.stopped.lock().unwrap_or_else(PoisonError::into_inner);
        let (guard, _) = self.wake.wait_timeout_while(guard, span, |stopped| !*stopped).unwrap_or_else(PoisonError::into_inner);
        *guard
    }

    fn stop(&self) {
        *self.stopped.lock().unwrap_or_else(PoisonError::into_inner) = true;
        self.wake.notify_all();
    }

    /// Write and flush; a closed terminal stops the loader instead of printing anywhere else.
    fn write(&self, text: &str) -> bool {
        if text.is_empty() {
            return true;
        }
        let mut out = self.out.lock().unwrap_or_else(PoisonError::into_inner);
        let written = out.write_all(text.as_bytes()).and_then(|()| out.flush()).is_ok();
        drop(out);
        if !written {
            self.stop();
        }
        written
    }

    /// The escape code that moves the cursor back to the top of the drawing.
    fn rewind(&self) -> String {
        match self.lines.load(Ordering::SeqCst) {
            0 => String::new(),
            lines => format!("\x1b[{lines}F"),
        }
    }

    /// Redraw in place over the previous frame, clearing the rest of each line so a shorter line leaves nothing behind.
    fn draw(&self, lines: &[String]) -> bool {
        let text = lines.join("\x1b[K\n") + "\x1b[K\n";
        let written = self.write(&format!("{}{text}", self.rewind()));
        self.lines.store(lines.len(), Ordering::SeqCst);
        written
    }

    /// Remove the drawing so the report prints on a clean screen.
    fn erase(&self) {
        if self.lines.load(Ordering::SeqCst) == 0 {
            return;
        }
        self.write(&format!("{}\x1b[J", self.rewind()));
        self.lines.store(0, Ordering::SeqCst);
    }

    /// The side thread: wait, hide the cursor, then move the arrow one step per tick toward the target.
    fn run(&self) {
        if self.wait(SHOW_AFTER) || !self.write(HIDE_CURSOR) {
            return;
        }
        self.hidden.store(true, Ordering::SeqCst);
        let mut step: usize = 0;
        while !self.wait(TICK) {
            let (done, total) = (self.done.load(Ordering::SeqCst), self.total.load(Ordering::SeqCst));
            step = step.saturating_add(1).min(target_step(done, total));
            let stage = self.stage.lock().unwrap_or_else(PoisonError::into_inner).clone();
            if !self.draw(&with_stage(frame(step, true, LAVENDER, self.color), &stage, done, total, self.color)) {
                return;
            }
        }
    }
}

/// Draws from a side thread; the run calls [`Loader::update`], and dropping it (or [`Loader::finish`]) ends and erases the drawing.
pub struct Loader {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
    finished: bool,
}

impl Loader {
    /// Start drawing on standard error (the first frame waits 0.3 s).
    #[must_use]
    pub fn start(color: bool) -> Self {
        Self::with_output(color, Box::new(io::stderr()))
    }

    fn with_output(color: bool, out: Box<dyn Write + Send>) -> Self {
        let shared = Arc::new(Shared {
            stage: Mutex::new(String::new()),
            done: AtomicUsize::new(0),
            total: AtomicUsize::new(1),
            lines: AtomicUsize::new(0),
            hidden: AtomicBool::new(false),
            stopped: Mutex::new(false),
            wake: Condvar::new(),
            color,
            out: Mutex::new(out),
        });
        let worker = Arc::clone(&shared);
        Self { shared, thread: Some(thread::spawn(move || worker.run())), finished: false }
    }

    /// Name the stage the run is in; it is drawn beside the crystal.
    pub fn stage(&self, text: &str) {
        text.clone_into(&mut self.shared.stage.lock().unwrap_or_else(PoisonError::into_inner));
    }

    /// Record how many files are done; the side thread reads these two numbers.
    pub fn update(&self, done: usize, total: usize) {
        self.shared.done.store(done, Ordering::SeqCst);
        self.shared.total.store(total, Ordering::SeqCst);
    }

    /// The run succeeded: show the closed crystal before it is erased.
    pub fn finish(mut self) {
        self.finished = true;
    }
}

impl Drop for Loader {
    /// End the side thread, flash the closed crystal when the run finished, erase it, and give the cursor back.
    fn drop(&mut self) {
        self.shared.stop();
        if let Some(thread) = self.thread.take() {
            drop(thread.join());
        }
        if self.shared.lines.load(Ordering::SeqCst) > 0 && self.finished {
            for tint in [BRIGHT, LAVENDER, BRIGHT] {
                self.shared.draw(&frame(STEPS, false, tint, self.shared.color));
                thread::sleep(FLASH);
            }
        }
        self.shared.erase();
        if self.shared.hidden.load(Ordering::SeqCst) {
            self.shared.write(SHOW_CURSOR);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A writer the test can read back.
    #[derive(Clone, Default)]
    struct Capture(Arc<Mutex<Vec<u8>>>);

    impl Write for Capture {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.0.lock().unwrap_or_else(PoisonError::into_inner).extend_from_slice(bytes);
            Ok(bytes.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    impl Capture {
        fn text(&self) -> String {
            String::from_utf8_lossy(&self.0.lock().unwrap()).into_owned()
        }
    }

    fn terminal() -> Environment {
        Environment { is_terminal: true, term: Some("xterm".to_string()), in_ci: false, opted_out: false }
    }

    // The arrow never reaches the end, however many files are done.
    #[test]
    fn test_target_step() {
        assert_eq!(target_step(0, 10), 0, "nothing done yet");
        assert_eq!(target_step(5, 10), 6, "half done");
        assert_eq!(target_step(10, 10), STEPS - 1, "all done still stops one short");
        assert_eq!(target_step(3, 0), 0, "no files is not a division by zero");
    }

    // A finished crystal is the outline of the prism and nothing else.
    #[test]
    fn test_closed_frame() {
        let lines = frame(STEPS, false, LAVENDER, false);
        let expected: Vec<String> = PRISM.iter().map(|line| format!("  {line}")).collect();
        assert_eq!(lines, expected, "twelve cells draw the whole outline");
    }

    // The stage is written beside the crystal, with the file count only while it is counting.
    #[test]
    fn test_stage_beside() {
        let lines = with_stage(frame(3, true, LAVENDER, false), "Building the trees", 73, 145, false);
        let ends = |rows: &[String], n: usize, tail: &str| rows.get(n).is_some_and(|row| row.ends_with(tail));
        assert!(ends(&lines, 2, "   Building the trees") && ends(&lines, 3, "   73 of 145 files"), "stage and count");
        let scoring = with_stage(frame(11, true, LAVENDER, false), "Scoring on the Krypton server", 145, 145, false);
        assert!(ends(&scoring, 2, "Scoring on the Krypton server") && !scoring.iter().any(|row| row.contains("files")), "no count once all are done");
        assert_eq!(with_stage(frame(0, true, LAVENDER, false), "", 0, 1, false), frame(0, true, LAVENDER, false), "no stage, no text");
    }

    // The first frame is just the arrow, pointing down, and colour is optional.
    #[test]
    fn test_first_frame() {
        let plain = frame(0, true, LAVENDER, false);
        assert_eq!(plain.first().map(String::as_str), Some("     v    "), "the arrow sits at the top of the crystal");
        assert!(frame(1, true, LAVENDER, true).concat().contains("\x1b[38;2;199;181;236m"), "the outline is lavender");
    }

    // Nothing is drawn where it would get in the way.
    #[test]
    fn test_wanted_only_on_a_terminal() {
        assert_eq!(wanted(&terminal(), false), !cfg!(windows), "a capable terminal gets it (not a Windows console)");
        assert!(!wanted(&Environment { is_terminal: false, ..terminal() }, false), "a pipe gets nothing");
        assert!(!wanted(&Environment { term: Some("dumb".to_string()), ..terminal() }, false), "a dumb terminal gets nothing");
        assert!(!wanted(&Environment { in_ci: true, ..terminal() }, false), "CI gets nothing");
        assert!(!wanted(&Environment { opted_out: true, ..terminal() }, false), "KRYPTON_NO_LOADER switches it off");
        assert!(!wanted(&terminal(), true), "--no-loader switches it off");
    }

    // A quick run draws nothing at all, not even a cursor change.
    #[test]
    fn test_quick_run_silent() {
        let capture = Capture::default();
        let loader = Loader::with_output(true, Box::new(capture.clone()));
        loader.update(1, 1);
        loader.finish();
        assert_eq!(capture.text(), "", "nothing was written");
    }

    // A slow run draws lavender frames, then erases them and gives the cursor back.
    #[test]
    fn test_slow_run_cleans_up() {
        let capture = Capture::default();
        let loader = Loader::with_output(true, Box::new(capture.clone()));
        loader.update(6, 10);
        thread::sleep(Duration::from_millis(900));
        loader.finish();
        let text = capture.text();
        assert!(text.starts_with(HIDE_CURSOR), "the cursor is hidden first");
        assert!(text.contains("\x1b[38;2;199;181;236m"), "lavender frames were drawn");
        assert!(text.ends_with(SHOW_CURSOR), "the cursor is given back last");
        assert!(text.contains("\x1b[J"), "the drawing was erased");
    }

    // A failed run (the loader dropped without finish) still erases and restores the cursor, and skips the finishing flash.
    #[test]
    fn test_failed_run_cleans_up() {
        let capture = Capture::default();
        let loader = Loader::with_output(false, Box::new(capture.clone()));
        thread::sleep(Duration::from_millis(700));
        drop(loader);
        let text = capture.text();
        assert!(text.ends_with(SHOW_CURSOR), "the cursor is given back even on failure");
        assert!(!text.contains("\x1b[38;2"), "no colour when colour is off");
    }
}
