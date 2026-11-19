//! The update check. At most once a day, and only for a person at a terminal, Krypton asks GitHub which release is the latest; if it is newer
//! than this one, it asks "Update now?". A yes ends the run with [`UPDATE_EXIT`], and the `krypton` shell command answers that by pulling the
//! new code into your clone and rebuilding the image on your computer (a container cannot replace its own image). `krypton update` does the
//! same at any time.
//!
//! It is built never to get in the way: one anonymous GET of the latest release (nothing about you or your code), a two-second limit, and
//! no word at all when the network or GitHub is not there, since a missing update notice must never fail a scan. Never in CI, a pipe, or
//! with `KRYPTON_NO_UPDATE_CHECK`; a "no" is remembered for that version, so it does not ask again until the next one.

use std::env;
use std::fs;
use std::io::{self, BufRead, IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

/// The exit code that tells the `krypton` shell command to update the clone and rebuild the image.
pub const UPDATE_EXIT: u8 = 75;
/// Where to ask; `KRYPTON_UPDATE_URL` points it elsewhere (a mirror, or a test).
const LATEST: &str = "https://api.github.com/repos/pinklamb/Krypton/releases/latest";
const DAY: u64 = 86_400;
const STATE: &str = "update-check.json";

/// `3.1.0` or `v3.1.0` as numbers to compare.
#[must_use]
pub fn version(text: &str) -> Option<(u64, u64, u64)> {
    let mut parts = text.trim().trim_start_matches('v').split('.').map(|part| part.parse::<u64>().ok());
    Some((parts.next()??, parts.next()??, parts.next()??))
}

/// Whether `latest` is a newer release than `current`.
#[must_use]
pub fn newer(latest: &str, current: &str) -> bool {
    matches!((version(latest), version(current)), (Some(latest), Some(current)) if latest > current)
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |since| since.as_secs())
}

/// Only a person at a terminal is asked, and not in CI or when switched off.
fn wanted() -> bool {
    io::stdin().is_terminal() && io::stderr().is_terminal() && env::var_os("CI").is_none() && env::var_os("KRYPTON_NO_UPDATE_CHECK").is_none()
}

fn state_path() -> Option<PathBuf> {
    crate::login_command::home().ok().map(|home| home.join(STATE))
}

/// Set one field of the state, which is always an object (anything else read from disk starts over).
fn set(state: &mut Value, key: &str, value: Value) {
    if !state.is_object() {
        *state = json!({});
    }
    if let Some(map) = state.as_object_mut() {
        map.insert(key.to_string(), value);
    }
}

fn read_state(path: &Path) -> Value {
    fs::read_to_string(path).ok().and_then(|text| serde_json::from_str(&text).ok()).unwrap_or_else(|| json!({}))
}

fn write_state(path: &Path, state: &Value) {
    // a state file that cannot be written only means the next run asks GitHub again
    if let Some(dir) = path.parent() {
        if crate::runlog::ensure_ignored(dir).is_ok() {
            let _kept = fs::write(path, state.to_string()).is_ok();
        }
    }
}

/// The latest release's version, or `None` when it cannot be learned quickly.
fn fetch_latest() -> Option<String> {
    let url = env::var("KRYPTON_UPDATE_URL").unwrap_or_else(|_| LATEST.to_string());
    let reply = ureq::get(&url).set("User-Agent", "krypton-cli").timeout(Duration::from_secs(2)).call().ok()?;
    let body: Value = serde_json::from_str(&reply.into_string().ok()?).ok()?;
    body.get("tag_name").and_then(Value::as_str).map(|tag| tag.trim_start_matches('v').to_string())
}

/// What to do with what is known: the version to offer, if any. Asks GitHub only when the last check is a day old.
fn offer_for(state: &mut Value, current: &str, at: u64, fetch: impl FnOnce() -> Option<String>) -> Option<String> {
    let checked = state.get("checked").and_then(Value::as_u64).unwrap_or(0);
    if at.saturating_sub(checked) >= DAY {
        if let Some(latest) = fetch() {
            set(state, "latest", json!(latest));
        }
        set(state, "checked", json!(at));
    }
    let latest = state.get("latest").and_then(Value::as_str)?.to_string();
    let declined = state.get("declined").and_then(Value::as_str);
    (newer(&latest, current) && declined != Some(latest.as_str())).then_some(latest)
}

/// Whether the answer to "Update now? [Y/n]" is a yes; Enter alone is yes.
#[must_use]
pub fn is_yes(answer: &str) -> bool {
    matches!(answer.trim().to_ascii_lowercase().as_str(), "" | "y" | "yes")
}

fn ask(latest: &str, current: &str) -> bool {
    let mut err = io::stderr().lock();
    let asked = write!(err, "\nKrypton {latest} is out (you have {current}). Update now? [Y/n] ").and_then(|()| err.flush()).is_ok();
    drop(err);
    let mut answer = String::new();
    asked && io::stdin().lock().read_line(&mut answer).is_ok() && is_yes(&answer)
}

/// Check, and ask when there is something newer. True when the person said yes: the run should then end with [`UPDATE_EXIT`].
#[must_use]
pub fn offer() -> bool {
    if !wanted() {
        return false;
    }
    let Some(path) = state_path() else { return false };
    let mut state = read_state(&path);
    let current = env!("CARGO_PKG_VERSION");
    let latest = offer_for(&mut state, current, now(), fetch_latest);
    let yes = latest.as_deref().is_some_and(|latest| ask(latest, current));
    if let (Some(latest), false) = (&latest, yes) {
        set(&mut state, "declined", json!(latest));
    }
    write_state(&path, &state);
    if yes {
        eprintln!("Updating Krypton ...");
    }
    yes
}

/// `krypton update` run inside the container itself (not through the `krypton` shell command, which handles it on your computer).
pub fn run() -> u8 {
    eprintln!("Updating Krypton ...");
    UPDATE_EXIT
}

#[cfg(test)]
mod tests {
    use super::*;

    // Versions compare as numbers, with or without the v.
    #[test]
    fn test_newer() {
        assert!(newer("v3.1.0", "3.0.9") && newer("3.0.10", "3.0.9") && newer("4.0.0", "3.9.9"), "newer");
        assert!(!newer("3.0.0", "3.0.0") && !newer("2.9.0", "3.0.0") && !newer("garbage", "3.0.0"), "not newer");
    }

    // GitHub is asked at most once a day; a newer release is offered until it is declined.
    #[test]
    fn test_offer_for() {
        let mut state = json!({});
        assert_eq!(offer_for(&mut state, "3.0.0", DAY, || Some("3.1.0".to_string())), Some("3.1.0".to_string()), "first check finds it");
        assert_eq!(offer_for(&mut state, "3.0.0", DAY + 60, || panic!("asked twice in a day")), Some("3.1.0".to_string()), "remembered, not asked again");
        set(&mut state, "declined", json!("3.1.0"));
        assert_eq!(offer_for(&mut state, "3.0.0", DAY + 120, || None), None, "a no is kept for that version");
        assert_eq!(offer_for(&mut state, "3.0.0", 3 * DAY, || Some("3.2.0".to_string())), Some("3.2.0".to_string()), "the next release is offered");
        assert_eq!(offer_for(&mut json!({}), "3.0.0", DAY, || None), None, "no network: nothing to offer, no error");
    }

    // Enter alone means yes.
    #[test]
    fn test_is_yes() {
        assert!(is_yes("\n") && is_yes("Y") && is_yes("yes ") && !is_yes("n") && !is_yes("no"), "yes and no");
    }
}
