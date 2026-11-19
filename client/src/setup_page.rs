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

fn read_request(stream: &TcpStream) -> Result<Request, String> {
    let mut reader = BufReader::new(stream);
    let mut first = String::new();
    reader.read_line(&mut first).map_err(|error| error.to_string())?;
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
    Ok(Request { method, path, body: String::from_utf8_lossy(&body).into_owned() })
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
pub fn choose(listen: &str, current: &Settings) -> Result<Settings, Failure> {
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
                if let Some(settings) = answer(&stream, &key, current) {
                    return Ok(settings);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => watch.wait(Duration::from_millis(100))?,
            Err(error) => return Err(Failure::Setup(format!("the settings page on {listen} stopped before anything was saved: {error}"))),
        }
    }
}

/// Answer one browser request: the page, a refusal, or (when the form was sent and is valid) the saved settings.
fn answer(stream: &TcpStream, key: &str, current: &Settings) -> Option<Settings> {
    // the listener does not block, but each request is read and answered in one go
    if stream.set_nonblocking(false).is_err() {
        return None;
    }
    let request = match read_request(stream) {
        Ok(request) => request,
        Err(error) => {
            respond(stream, "400 Bad Request", &page(current, Some(&error)));
            return None;
        }
    };
    if request.path != key {
        respond(stream, "404 Not Found", "<p>Not here. Use the link krypton setup printed in your terminal.</p>");
        return None;
    }
    if request.method != "POST" {
        respond(stream, "200 OK", &page(current, None));
        return None;
    }
    match parse_form(&request.body, current.baseline) {
        Ok(settings) => {
            respond(stream, "200 OK", SAVED);
            Some(settings)
        }
        Err(problem) => {
            respond(stream, "400 Bad Request", &page(current, Some(&problem)));
            None
        }
    }
}

const STYLE: &str = "body{margin:0;background:#1b1613;color:#ede4da;font:300 16px/1.6 system-ui,-apple-system,'Segoe UI',sans-serif}
main{max-width:560px;margin:0 auto;padding:56px 16px}
h1{font-weight:300;font-size:40px;margin:0 0 4px}h1 small{font-size:16px;color:#a89c90}
p{color:#cfc4b8}b{color:#c7b5ec;font-weight:500}.card{background:#251e1a;border-radius:18px;padding:28px;margin-top:28px;box-shadow:0 6px 24px rgba(0,0,0,.3)}
fieldset{border:0;margin:0 0 22px;padding:0}legend{font-style:italic;color:#cfc4b8;margin-bottom:8px}
label{display:inline-block;margin:0 6px 6px 0}input[type=radio]{position:absolute;opacity:0}
label span{display:inline-block;padding:6px 16px;border-radius:999px;border:1px solid #4a3f37;background:#1b1613;color:#ede4da;cursor:pointer}
input:checked+span{background:#c7b5ec;border-color:#c7b5ec;color:#1b1613}input:focus-visible+span{outline:2px solid #c7b5ec;outline-offset:2px}
input[type=number]{font:inherit;width:90px;padding:6px 12px;border-radius:999px;border:1px solid #4a3f37;background:#1b1613;color:#ede4da}
button{font:inherit;background:#c7b5ec;color:#1b1613;border:0;border-radius:999px;padding:10px 26px;cursor:pointer}
.err{color:#e59a9a}.hint{font-size:14px;color:#a89c90}";

fn choice(name: &str, value: &str, text: &str, checked: bool) -> String {
    let on = if checked { " checked" } else { "" };
    format!("<label><input type=radio name={name} value={value}{on}><span>{text}</span></label>")
}

fn page(current: &Settings, problem: Option<&str>) -> String {
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
        "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Krypton setup</title><style>{STYLE}</style></head>
<body><main><h1>Kr<small>ypton</small> setup</h1>{today}{error}<form method=post class=card>
<fieldset><legend>Target score for each ticket</legend><input type=number name=target min=0 max=100 value={target} required></fieldset>
<fieldset><legend>Max tries on a function before it counts as stuck</legend>{runs}</fieldset>
<fieldset><legend>When the agent is still stuck</legend>{modes}<p class=hint>Ask me: your agent builds a review page for the current ticket, styled by themes.md, and you decide before anything goes to GitHub. Auto: Krypton keeps the best run and the agent carries on.</p></fieldset>
<fieldset><legend>Pull request gate on GitHub</legend>{gates}<p class=hint>Writes .github/workflows/krypton.yml; commit it to turn the gate on. Never worse: a pull request fails only if it lowers the purity score. Target: the files a pull request changes must reach your target; old code is never judged. An existing workflow is never overwritten.</p></fieldset>
<button type=submit>Save settings</button></form></main></body></html>",
        target = current.target
    )
}

const SAVED: &str = "<!doctype html><html lang=en><head><meta charset=utf-8><title>Krypton setup</title></head><body style=\"background:#1b1613;font:300 18px system-ui,sans-serif;color:#ede4da;padding:56px 16px;text-align:center\"><p>Saved. You can close this tab; the rest is in your terminal.</p></body></html>";

#[cfg(test)]
mod tests {
    use super::{page, parse_form, secret};
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
        let html = page(&Settings { mode: Mode::Auto, target: 72, max_runs: 3, baseline: Some(28), gate: Gate::Worse }, None);
        assert!(html.contains("value=72 required"), "the target is filled in");
        assert!(html.contains("value=auto checked"), "the mode is chosen");
        assert!(html.contains("<b>28</b>"), "the baseline is shown");
        assert!(html.contains("value=worse checked"), "the gate is chosen");
    }

    // Two links are never the same.
    #[test]
    fn test_secret() {
        assert_ne!(secret(), secret(), "each link is new");
        assert_eq!(secret().len(), 32, "128 bits in hex");
    }
}
