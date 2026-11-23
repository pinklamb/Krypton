//! The settings page of `krypton setup --page`: a one-time page on this computer where a person clicks the settings instead of typing them.
//!
//! It is a tiny HTTP server built on the standard library. It listens on `127.0.0.1:7777` (or `KRYPTON_LISTEN`, which the Docker image sets
//! to `0.0.0.0:7777` so a port published as `-p 127.0.0.1:7777:7777` reaches it), serves one page at a secret link, and stops as soon as
//! the settings are saved. The secret in the link keeps other web pages open in the same browser from changing the settings.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::setup_command::{parse_runs, parse_target, Mode, Settings};
use crate::workflow::Gate;
use crate::Failure;

/// Where the page listens when `KRYPTON_LISTEN` is not set: this computer only.
pub const LISTEN: &str = "127.0.0.1:7777";
/// A settings form is a few dozen bytes; anything much larger is not ours.
const MOST_BODY: usize = 4096;
/// How long a connection may stay quiet before it is dropped. Browsers open spare connections ahead of time and often send nothing on
/// them; waiting on one without a limit would leave the real request (the Save) unanswered behind it.
const QUIET: Duration = Duration::from_secs(2);

/// A secret for the link: 128 bits from the standard library's randomly keyed hasher, mixed with the time and process.
fn secret() -> String {
    let part = |salt: u64| {
        let mut hasher = RandomState::new().build_hasher();
        hasher.write_u64(salt);
        hasher.write_u128(SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |time| time.as_nanos()));
        hasher.write_u32(std::process::id());
        hasher.finish()
    };
    format!("{:016x}{:016x}", part(1), part(2))
}

/// One request as far as the page needs it: the method, the path and the form body.
struct Request {
    method: String,
    path: String,
    body: String,
}

/// A connection that went quiet: a browser's spare connection, or one it gave up on.
fn quiet(error: &std::io::Error) -> bool {
    matches!(error.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut)
}

/// The request on this connection, `None` when the browser sent nothing (a spare connection), or what is wrong with it.
fn read_request(stream: &TcpStream) -> Result<Option<Request>, String> {
    let mut reader = BufReader::new(stream);
    let mut first = String::new();
    match reader.read_line(&mut first) {
        Ok(0) => return Ok(None),
        Err(error) if quiet(&error) => return Ok(None),
        Err(error) => return Err(error.to_string()),
        Ok(_) => {}
    }
    let mut words = first.split_whitespace();
    let (method, path) = (words.next().unwrap_or_default().to_string(), words.next().unwrap_or_default().to_string());
    let mut length = 0;
    loop {
        let mut line = String::new();
        reader.read_line(&mut line).map_err(|error| error.to_string())?;
        if line.trim().is_empty() {
            break;
        }
        if let Some((name, value)) = line.split_once(':').filter(|(name, _)| name.eq_ignore_ascii_case("content-length")) {
            length = value.trim().parse().map_err(|error| format!("bad {name}: {error}"))?;
        }
    }
    if length > MOST_BODY {
        return Err("form too large".to_string());
    }
    let mut body = vec![0; length];
    reader.read_exact(&mut body).map_err(|error| error.to_string())?;
    Ok(Some(Request { method, path, body: String::from_utf8_lossy(&body).into_owned() }))
}

/// The four settings from a submitted form (`target=70&runs=3&mode=manual&gate=worse`), or what is wrong with it.
pub fn parse_form(body: &str, baseline: Option<i64>) -> Result<Settings, String> {
    let field = |key: &str| body.split('&').filter_map(|pair| pair.split_once('=')).find(|(name, _)| *name == key).map(|(_, value)| value.trim().to_string());
    let target = field("target").as_deref().and_then(parse_target).ok_or("The target is a whole number from 0 to 100.")?;
    let max_runs = field("runs").as_deref().and_then(parse_runs).ok_or("Max tries is a whole number from 1 to 10.")?;
    let mode = field("mode").as_deref().and_then(Mode::parse).ok_or("Choose Ask me or Auto.")?;
    let gate = field("gate").as_deref().and_then(Gate::parse).ok_or("Choose whether to set up the pull request gate.")?;
    Ok(Settings { mode, target, max_runs, baseline, gate })
}

fn respond(mut stream: &TcpStream, status: &str, html: &str) {
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
        html.len()
    );
    // The browser may already have gone; the page's own state does not depend on it reading the answer.
    let sent = stream.write_all(head.as_bytes()).and_then(|()| stream.write_all(html.as_bytes()));
    if let Err(error) = sent {
        eprintln!("the settings page could not answer the browser: {error}");
    }
}

/// Serve the page until the settings are submitted, and return them.
///
/// # Errors
/// When the address cannot be listened on (for example the port is taken).
pub fn choose(listen: &str, current: &Settings, theme: Theme) -> Result<Settings, Failure> {
    let listener = TcpListener::bind(listen).map_err(|error| Failure::Setup(format!("cannot open the settings page on {listen}: {error}")))?;
    let port = listen.rsplit(':').next().unwrap_or_default();
    let key = format!("/{}", secret());
    println!(
        "\nOpen this link to choose your settings (it works once, on this computer):\n\n  http://localhost:{port}{key}\n\nPress Ctrl+C or Esc to cancel.\n"
    );
    // Not blocking: the loop takes turns between the browser (a request waiting?) and the keyboard (Ctrl+C or Esc in the next 100 ms?),
    // so neither one freezes the other. The keyboard is watched (raw mode) until this function returns; nothing is printed meanwhile.
    listener.set_nonblocking(true).map_err(|error| Failure::Setup(format!("cannot open the settings page on {listen}: {error}")))?;
    let watch = crate::cancel::Watch::start();
    loop {
        match listener.accept() {
            Ok((stream, _from)) => {
                if let Some(settings) = answer(&stream, &key, current, theme) {
                    return Ok(settings);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => watch.wait(Duration::from_millis(100))?,
            Err(error) => return Err(Failure::Setup(format!("the settings page on {listen} stopped before anything was saved: {error}"))),
        }
    }
}

/// Answer one browser request: the page, a refusal, or (when the form was sent and is valid) the saved settings.
fn answer(stream: &TcpStream, key: &str, current: &Settings, theme: Theme) -> Option<Settings> {
    // the listener does not block, but each request is read and answered in one go, giving up on a connection that stays quiet
    if stream.set_nonblocking(false).and_then(|()| stream.set_read_timeout(Some(QUIET))).is_err() {
        return None;
    }
    let request = match read_request(stream) {
        Ok(Some(request)) => request,
        Ok(None) => return None,
        Err(error) => {
            respond(stream, "400 Bad Request", &page(current, Some(&error), theme));
            return None;
        }
    };
    if request.path != key {
        respond(stream, "404 Not Found", "<p>Not here. Use the link krypton setup printed in your terminal.</p>");
        return None;
    }
    if request.method != "POST" {
        respond(stream, "200 OK", &page(current, None, theme));
        return None;
    }
    match parse_form(&request.body, current.baseline) {
        Ok(settings) => {
            respond(stream, "200 OK", &saved(theme));
            Some(settings)
        }
        Err(problem) => {
            respond(stream, "400 Bad Request", &page(current, Some(&problem), theme));
            None
        }
    }
}

/// The page's look. Cocoa is Krypton's own (dark, lavender accent); blossom is cherry blossom (soft pink, deep rose accent), picked with
/// `krypton setup --page --theme blossom`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Theme {
    #[default]
    Cocoa,
    Blossom,
}

impl Theme {
    /// `cocoa`, or `blossom` (also `cherry-blossom`).
    #[must_use]
    pub fn parse(text: &str) -> Option<Self> {
        match text {
            "cocoa" => Some(Self::Cocoa),
            "blossom" | "cherry-blossom" => Some(Self::Blossom),
            _ => None,
        }
    }

    /// The colours the page's style reads.
    const fn colors(self) -> &'static str {
        match self {
            Self::Cocoa => COCOA,
            Self::Blossom => BLOSSOM,
        }
    }

    /// A mark before the heading: a five-petal blossom on the blossom page, nothing on cocoa.
    const fn mark(self) -> &'static str {
        match self {
            Self::Cocoa => "",
            Self::Blossom => FLOWER,
        }
    }
}

const COCOA: &str = ":root{color-scheme:dark;--bg:#1b1613;--wash:none;--card:#251e1a;--field:#1b1613;--ink:#ede4da;--ink2:#cfc4b8;--mute:#a89c90;\
--line:#4a3f37;--acc:#c7b5ec;--on-acc:#1b1613;--err:#e59a9a;--shadow:rgba(0,0,0,.3)}";
const BLOSSOM: &str = ":root{color-scheme:light;--bg:#fcf0f3;--wash:radial-gradient(circle at 8% 4%,#f8d3de 0,transparent 32%),\
radial-gradient(circle at 96% 92%,#f9dbe4 0,transparent 38%);--card:#fffafb;--field:#fff4f7;--ink:#3b2129;--ink2:#5e3a45;--mute:#94707a;\
--line:#efc9d5;--acc:#c13f63;--on-acc:#fff;--err:#b3261e;--shadow:rgba(193,63,99,.12)}";
const FLOWER: &str = "<svg class=mark viewBox='-12 -12 24 24' aria-hidden=true><g fill='#f4a7bb'><ellipse cx=0 cy=-6 rx=4 ry=6/>\
<ellipse cx=0 cy=-6 rx=4 ry=6 transform='rotate(72)'/><ellipse cx=0 cy=-6 rx=4 ry=6 transform='rotate(144)'/>\
<ellipse cx=0 cy=-6 rx=4 ry=6 transform='rotate(216)'/><ellipse cx=0 cy=-6 rx=4 ry=6 transform='rotate(288)'/></g>\
<circle r=2.4 fill='#c13f63'/></svg>";

const STYLE: &str = "body{margin:0;background:var(--bg);background-image:var(--wash);color:var(--ink);font:300 16px/1.6 system-ui,-apple-system,'Segoe UI',sans-serif;min-height:100vh}
main{max-width:560px;margin:0 auto;padding:56px 16px}
h1{font-weight:300;font-size:40px;margin:0 0 4px;display:flex;align-items:center;gap:12px}h1 small{font-size:16px;color:var(--mute)}.mark{width:34px;height:34px}
p{color:var(--ink2)}b{color:var(--acc);font-weight:500}.card{background:var(--card);border-radius:18px;padding:28px;margin-top:28px;box-shadow:0 6px 24px var(--shadow)}
fieldset{border:0;margin:0 0 22px;padding:0}legend{font-style:italic;color:var(--ink2);margin-bottom:8px}
label{display:inline-block;margin:0 6px 6px 0}input[type=radio]{position:absolute;opacity:0}
label span{display:inline-block;padding:6px 16px;border-radius:999px;border:1px solid var(--line);background:var(--field);color:var(--ink);cursor:pointer}
input:checked+span{background:var(--acc);border-color:var(--acc);color:var(--on-acc)}input:focus-visible+span{outline:2px solid var(--acc);outline-offset:2px}
input[type=number]{font:inherit;width:90px;padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--field);color:var(--ink)}
button{font:inherit;background:var(--acc);color:var(--on-acc);border:0;border-radius:999px;padding:10px 26px;cursor:pointer}
.err{color:var(--err)}.hint{font-size:14px;color:var(--mute)}.done{text-align:center;font-size:18px}";

/// The page's head: the theme's colours and the shared style.
fn head(theme: Theme) -> String {
    format!(
        "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Krypton setup</title><style>{}{STYLE}</style></head>",
        theme.colors()
    )
}

fn choice(name: &str, value: &str, text: &str, checked: bool) -> String {
    let on = if checked { " checked" } else { "" };
    format!("<label><input type=radio name={name} value={value}{on}><span>{text}</span></label>")
}

fn page(current: &Settings, problem: Option<&str>, theme: Theme) -> String {
    let today = current.baseline.map_or_else(String::new, |score| {
        format!(
            "<p>Your codebase scores <b>{score}</b>/100 today. Krypton suggests <b>{}</b>, a step above: only new tickets are held to it, so \
             new work slowly cleans up the old and old code is never blocked.</p>",
            current.target
        )
    });
    let error = problem.map_or_else(String::new, |text| format!("<p class=err>{text}</p>"));
    let runs: String = [1, 2, 3, 5, 10].iter().map(|runs| choice("runs", &runs.to_string(), &runs.to_string(), *runs == current.max_runs)).collect();
    let modes = choice("mode", "manual", "Ask me", current.mode == Mode::Manual) + &choice("mode", "auto", "Auto", current.mode == Mode::Auto);
    let gates: String = [(Gate::Off, "Not now"), (Gate::Worse, "Never worse"), (Gate::Target, "Target")]
        .iter()
        .map(|(gate, text)| choice("gate", gate.name(), text, *gate == current.gate))
        .collect();
    format!(
        "{head}
<body><main><h1>{mark}<span>Kr<small>ypton</small> setup</span></h1>{today}{error}<form method=post class=card>
<fieldset><legend>Target score for each ticket</legend><input type=number name=target min=0 max=100 value={target} required></fieldset>
<fieldset><legend>Max tries on a function before it counts as stuck</legend>{runs}</fieldset>
<fieldset><legend>When the agent is still stuck</legend>{modes}<p class=hint>Ask me: your agent builds a review page for the current ticket, styled by themes.md, and you decide before anything goes to GitHub. Auto: Krypton keeps the best run and the agent carries on.</p></fieldset>
<fieldset><legend>Pull request gate on GitHub</legend>{gates}<p class=hint>Writes .github/workflows/krypton.yml; commit it to turn the gate on. Never worse: a pull request fails only if it lowers the purity score. Target: the files a pull request changes must reach your target; old code is never judged. An existing workflow is never overwritten.</p></fieldset>
<button type=submit>Save settings</button></form></main></body></html>",
        target = current.target,
        head = head(theme),
        mark = theme.mark()
    )
}

/// What the browser shows once the settings are saved.
fn saved(theme: Theme) -> String {
    format!("{}<body><main><p class=done>Saved. You can close this tab; the rest is in your terminal.</p></main></body></html>", head(theme))
}

#[cfg(test)]
mod tests {
    use super::{page, parse_form, secret, Theme};
    use crate::setup_command::{Mode, Settings};
    use crate::workflow::Gate;

    // A submitted form becomes the settings, and a wrong one says what is wrong.
    #[test]
    fn test_parse_form() {
        let settings = parse_form("target=72&runs=4&mode=auto&gate=target", Some(28)).unwrap();
        assert_eq!(settings, Settings { mode: Mode::Auto, target: 72, max_runs: 4, baseline: Some(28), gate: Gate::Target }, "all four read");
        assert!(parse_form("target=150&runs=4&mode=auto", None).is_err(), "a target over 100 is refused");
        assert!(parse_form("target=70&runs=3&gate=worse", None).is_err(), "a missing mode is refused");
        assert!(parse_form("target=70&runs=3&mode=auto", None).is_err(), "a missing gate is refused");
    }

    // The page starts from the current settings and shows the baseline.
    #[test]
    fn test_page() {
        let html = page(&Settings { mode: Mode::Auto, target: 72, max_runs: 3, baseline: Some(28), gate: Gate::Worse }, None, Theme::Cocoa);
        assert!(html.contains("value=72 required"), "the target is filled in");
        assert!(html.contains("value=auto checked"), "the mode is chosen");
        assert!(html.contains("<b>28</b>"), "the baseline is shown");
        assert!(html.contains("value=worse checked"), "the gate is chosen");
    }

    // The blossom theme swaps the colours and adds the blossom; cocoa stays Krypton's own.
    #[test]
    fn test_page_theme() {
        let settings = Settings { mode: Mode::Manual, target: 70, max_runs: 3, baseline: None, gate: Gate::Worse };
        let blossom = page(&settings, None, Theme::Blossom);
        assert!(blossom.contains("--bg:#fcf0f3") && blossom.contains("<svg class=mark"), "pink, with the blossom");
        let cocoa = page(&settings, None, Theme::Cocoa);
        assert!(cocoa.contains("--bg:#1b1613") && !cocoa.contains("<svg class=mark"), "cocoa, no blossom");
        assert_eq!(
            [Theme::parse("blossom"), Theme::parse("cherry-blossom"), Theme::parse("pink")],
            [Some(Theme::Blossom), Some(Theme::Blossom), None],
            "names"
        );
    }

    // A spare connection that sends nothing is dropped after a short wait, so the request behind it is still answered.
    #[test]
    fn test_quiet_connection() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let _spare = std::net::TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (stream, _) = listener.accept().unwrap();
        let settings = Settings { mode: Mode::Manual, target: 70, max_runs: 3, baseline: None, gate: Gate::Worse };
        let started = std::time::Instant::now();
        assert_eq!(super::answer(&stream, "/key", &settings, Theme::Cocoa), None, "nothing saved");
        assert!(started.elapsed() < std::time::Duration::from_secs(5), "given up on after the quiet limit, not waited on forever");
    }

    // Two links are never the same.
    #[test]
    fn test_secret() {
        assert_ne!(secret(), secret(), "each link is new");
        assert_eq!(secret().len(), 32, "128 bits in hex");
    }
}
