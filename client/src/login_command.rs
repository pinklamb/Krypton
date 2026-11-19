//! `krypton login [--server URL] [--github-actions]` and `krypton logout`: get the Krypton token the server needs to score.
//!
//! On a person's machine, login is GitHub's device flow (the one `gh auth login` uses): Krypton prints a short code, you enter it at
//! github.com/login/device and approve, and the server trades GitHub's answer for a Krypton token. Inside a GitHub Actions run,
//! `--github-actions` trades the run's own OIDC identity (it needs `permissions: id-token: write`) for a one-hour token, so CI stores no secret.
//!
//! The GitHub token is used once and never written down; only the Krypton token is kept, in `$KRYPTON_HOME/credentials` (default
//! `~/.krypton`; the Docker image uses `/krypton-home`, which the README's helper keeps in a named volume). `KRYPTON_TOKEN` overrides the file.

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::PathBuf;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::http::Server;
use crate::Failure;

const USAGE: &str = "usage: krypton login [--server URL] [--github-actions] | krypton logout [--server URL]";
const DEVICE_CODE: &str = "https://github.com/login/device/code";
const ACCESS_TOKEN: &str = "https://github.com/login/oauth/access_token";
const DEVICE_GRANT: &str = "urn:ietf:params:oauth:grant-type:device_code";
const CREDENTIALS: &str = "credentials";

/// Where the token is kept: `$KRYPTON_HOME`, else `~/.krypton`.
///
/// # Errors
///
/// [`Failure::Usage`] when neither `KRYPTON_HOME` nor a home folder is known.
pub fn home() -> Result<PathBuf, Failure> {
    if let Some(dir) = env::var_os("KRYPTON_HOME").filter(|dir| !dir.is_empty()) {
        return Ok(PathBuf::from(dir));
    }
    env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(|dir| PathBuf::from(dir).join(".krypton"))
        .ok_or_else(|| Failure::Usage("set KRYPTON_HOME: there is no home folder to keep the login in".to_string()))
}

/// Saved tokens, one per server address.
type Tokens = BTreeMap<String, String>;

fn read_tokens(dir: &std::path::Path) -> Result<Tokens, Failure> {
    let path = dir.join(CREDENTIALS);
    if !path.exists() {
        return Ok(Tokens::new());
    }
    let text = fs::read_to_string(&path).map_err(|error| Failure::Read(path.display().to_string(), error))?;
    serde_json::from_str(&text).map_err(|error| Failure::Usage(format!("{} is damaged ({error}); run `krypton logout` and log in again", path.display())))
}

fn write_tokens(dir: &std::path::Path, tokens: &Tokens) -> Result<(), Failure> {
    // ignored by git like `.krypton`, in case KRYPTON_HOME points inside a repository
    crate::runlog::ensure_ignored(dir).map_err(|error| Failure::Log(format!("cannot create {}: {error}", dir.display())))?;
    let path = dir.join(CREDENTIALS);
    let text = serde_json::to_string_pretty(tokens).map_err(|error| Failure::Output(error.to_string()))?;
    fs::write(&path, text).map_err(|error| Failure::Log(format!("cannot write {}: {error}", path.display())))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).map_err(|error| Failure::Log(format!("cannot protect {}: {error}", path.display())))?;
    }
    Ok(())
}

/// The token for `server`: `KRYPTON_TOKEN`, else the saved one, else none.
///
/// # Errors
///
/// When the credentials file exists but cannot be read.
pub fn token_for(server: &str) -> Result<Option<String>, Failure> {
    if let Some(token) = env::var("KRYPTON_TOKEN").ok().filter(|token| !token.trim().is_empty()) {
        return Ok(Some(token.trim().to_string()));
    }
    Ok(read_tokens(&home()?)?.get(server).cloned())
}

fn save(server: &str, token: &str) -> Result<(), Failure> {
    let dir = home()?;
    let mut tokens = read_tokens(&dir)?;
    tokens.insert(server.to_string(), token.to_string());
    write_tokens(&dir, &tokens)
}

/// What GitHub's answer to one poll means.
#[derive(Debug, PartialEq, Eq)]
enum Poll {
    Token(String),
    Wait,
    Slower,
    Stop(String),
}

fn read_poll(answer: &Value) -> Poll {
    if let Some(token) = answer.get("access_token").and_then(Value::as_str) {
        return Poll::Token(token.to_string());
    }
    match answer.get("error").and_then(Value::as_str).unwrap_or_default() {
        "authorization_pending" => Poll::Wait,
        "slow_down" => Poll::Slower,
        "expired_token" => Poll::Stop("the code expired before it was approved; run `krypton login` again".to_string()),
        "access_denied" => Poll::Stop("the login was cancelled on GitHub".to_string()),
        other => Poll::Stop(format!("GitHub refused the login: {other}")),
    }
}

fn github_form(url: &str, form: &[(&str, &str)]) -> Result<Value, Failure> {
    let reply = ureq::post(url)
        .timeout(Duration::from_secs(30))
        .set("Accept", "application/json")
        .send_form(form)
        .map_err(|error| Failure::Unreachable(format!("could not reach GitHub: {error}")))?;
    crate::http::json_of(reply, "GitHub")
}

fn text_of<'a>(answer: &'a Value, key: &str) -> Result<&'a str, Failure> {
    answer.get(key).and_then(Value::as_str).ok_or_else(|| Failure::Server(format!("GitHub's answer has no {key}: {answer}")))
}

/// GitHub's device flow: show the code, wait for the person to approve it, return GitHub's token.
fn device_flow(client_id: &str) -> Result<String, Failure> {
    let start = github_form(DEVICE_CODE, &[("client_id", client_id)])?;
    let (device_code, user_code, url) = (text_of(&start, "device_code")?, text_of(&start, "user_code")?, text_of(&start, "verification_uri")?);
    let mut every = Duration::from_secs(start.get("interval").and_then(Value::as_u64).unwrap_or(5));
    let expires = Duration::from_secs(start.get("expires_in").and_then(Value::as_u64).unwrap_or(900));
    eprintln!("\nOpen {url} and enter the code:\n\n    {user_code}\n\nWaiting for you to approve it on GitHub ...");
    let started = Instant::now();
    while started.elapsed() < expires {
        thread::sleep(every);
        match read_poll(&github_form(ACCESS_TOKEN, &[("client_id", client_id), ("device_code", device_code), ("grant_type", DEVICE_GRANT)])?) {
            Poll::Token(token) => return Ok(token),
            Poll::Wait => {}
            Poll::Slower => every = every.saturating_add(Duration::from_secs(5)),
            Poll::Stop(message) => return Err(Failure::Refused(message)),
        }
    }
    Err(Failure::Refused("the code expired before it was approved; run `krypton login` again".to_string()))
}

/// The run's GitHub Actions identity token, for the audience `krypton`.
fn actions_identity() -> Result<String, Failure> {
    let missing = || Failure::Usage("no GitHub Actions identity: add `permissions: id-token: write` to the workflow".to_string());
    let url = env::var("ACTIONS_ID_TOKEN_REQUEST_URL").map_err(|_unset| missing())?;
    let bearer = env::var("ACTIONS_ID_TOKEN_REQUEST_TOKEN").map_err(|_unset| missing())?;
    let reply = ureq::get(&format!("{url}&audience=krypton"))
        .timeout(Duration::from_secs(30))
        .set("Authorization", &format!("Bearer {bearer}"))
        .call()
        .map_err(|error| Failure::Refused(format!("GitHub Actions gave no identity token: {error}")))?;
    let answer = crate::http::json_of(reply, "GitHub Actions")?;
    answer.get("value").and_then(Value::as_str).map(ToString::to_string).ok_or_else(|| Failure::Server("GitHub Actions' answer has no token".to_string()))
}

struct Options {
    server: Option<String>,
    actions: bool,
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let mut options = Options { server: None, actions: false };
    let mut words = args.iter();
    while let Some(word) = words.next() {
        match word.as_str() {
            "--server" => options.server = Some(words.next().cloned().ok_or_else(|| Failure::Usage(format!("--server needs an address\n{USAGE}")))?),
            "--github-actions" => options.actions = true,
            other => return Err(Failure::Usage(format!("unknown option {other}\n{USAGE}"))),
        }
    }
    Ok(options)
}

/// Log in to `server` interactively and keep the token.
///
/// # Errors
///
/// When the server has no GitHub app configured, GitHub refuses or the code expires, or the token cannot be saved.
pub fn login_to(server: &Server) -> Result<(), Failure> {
    let capabilities = server.get("/v1/capabilities")?;
    let login = capabilities.get("login").cloned().unwrap_or(Value::Null);
    if login.get("required").and_then(Value::as_bool) == Some(false) {
        eprintln!("{} does not need a login.", server.url());
        return Ok(());
    }
    let client_id = login
        .get("github_client_id")
        .and_then(Value::as_str)
        .ok_or_else(|| Failure::Refused(format!("{} names no GitHub app to log in with (KRYPTON_GITHUB_CLIENT_ID on the server)", server.url())))?;
    let github_token = device_flow(client_id)?;
    let answer = server.post_anonymous("/v1/auth/github", &json!({ "github_token": github_token }))?;
    keep(server, &answer)
}

fn keep(server: &Server, answer: &Value) -> Result<(), Failure> {
    let token = answer.get("token").and_then(Value::as_str).ok_or_else(|| Failure::Server("the server's answer has no token".to_string()))?;
    save(server.url(), token)?;
    eprintln!("Logged in as {}.", answer.get("name").and_then(Value::as_str).unwrap_or("you"));
    Ok(())
}

/// Log in unless a token for this server is already at hand: `krypton setup` starts with this.
///
/// # Errors
///
/// As [`login_to`].
pub fn ensure(server: &Server) -> Result<(), Failure> {
    if token_for(server.url())?.is_some() {
        return Ok(());
    }
    login_to(server)
}

pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let server = Server::from_flag(options.server.as_deref());
    if options.actions {
        let answer = server.post_anonymous("/v1/auth/actions", &json!({ "oidc_token": actions_identity()? }))?;
        return keep(&server, &answer);
    }
    login_to(&server)
}

pub fn logout(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let server = Server::from_flag(options.server.as_deref());
    let dir = home()?;
    let mut tokens = read_tokens(&dir)?;
    if tokens.remove(server.url()).is_none() {
        eprintln!("You were not logged in to {}.", server.url());
        return Ok(());
    }
    write_tokens(&dir, &tokens)?;
    eprintln!("Logged out of {}.", server.url());
    Ok(())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{read_poll, read_tokens, write_tokens, Poll, Tokens};

    // GitHub's poll answers become wait, slow down, the token, or a clear stop.
    #[test]
    fn test_read_poll() {
        assert_eq!(read_poll(&json!({ "error": "authorization_pending" })), Poll::Wait, "not approved yet");
        assert_eq!(read_poll(&json!({ "error": "slow_down" })), Poll::Slower, "asked to slow down");
        assert_eq!(read_poll(&json!({ "access_token": "gho_x" })), Poll::Token("gho_x".to_string()), "approved");
        assert!(matches!(read_poll(&json!({ "error": "access_denied" })), Poll::Stop(_)), "cancelled");
    }

    // Tokens are kept per server and read back.
    #[test]
    fn test_tokens_saved() {
        let dir = std::env::temp_dir().join(format!("krypton-login-{}", std::process::id()));
        let tokens: Tokens = [("https://a".to_string(), "t1".to_string()), ("https://b".to_string(), "t2".to_string())].into_iter().collect();
        write_tokens(&dir, &tokens).unwrap();
        assert_eq!(read_tokens(&dir).unwrap(), tokens, "what was saved comes back");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
