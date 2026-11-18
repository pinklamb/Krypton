//! Token counts for `krypton order`: how long each file is in the tokens of the model the prompt is for. The order decision compares
//! prefix lengths, so the counts should come from that model's own tokenizer.
//!
//! * `--model gpt-4o` (and the other `OpenAI` models) counts here, offline, with the real vocabulary (`o200k_base` or `cl100k_base`).
//! * `--model claude-...` asks Anthropic's token counter with your own `ANTHROPIC_API_KEY`. That sends each file's text to Anthropic (your model
//!   provider, never to Krypton), so it only happens when you name a Claude model. Counts are cached by content in `.krypton/tokens.json`.
//! * No model: an estimate of 3.5 characters per token, and the command says so.
//!
//! Krypton cannot read the model off your requests, because it is not in their path: your agent or SDK calls the model, not this tool. An
//! integration that knows the model (an agent harness, an editor extension) passes it as `--model`; `KRYPTON_MODEL` sets it for a shell.

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tiktoken_rs::CoreBPE;

use crate::Failure;

const CHARS_PER_TOKEN: f64 = 3.5;
const COUNT_URL: &str = "https://api.anthropic.com/v1/messages/count_tokens";
const STORE: &str = ".krypton";

/// Where counts come from.
pub enum Counter {
    Estimate,
    Bpe { name: &'static str, bpe: Box<CoreBPE> },
    Anthropic(Box<Anthropic>),
}

/// Anthropic's counter for one model, with a cache of what it has already answered.
pub struct Anthropic {
    key: String,
    model: String,
    /// What the API adds to every message regardless of its text; taken off each count.
    overhead: u64,
    cache_path: PathBuf,
    counts: BTreeMap<String, u64>,
}

fn unknown(model: &str) -> Failure {
    Failure::Usage(format!(
        "no tokenizer is known for the model '{model}': use a claude-*, gpt-*, o1/o3/o4 model, or --tokenizer o200k_base|cl100k_base|estimate, or --tokens-from COUNTS.json"
    ))
}

fn bpe(name: &str) -> Result<Counter, Failure> {
    let (label, made) = match name {
        "o200k_base" => ("o200k_base", tiktoken_rs::o200k_base()),
        "cl100k_base" => ("cl100k_base", tiktoken_rs::cl100k_base()),
        other => return Err(Failure::Usage(format!("--tokenizer must be o200k_base, cl100k_base or estimate, not '{other}'"))),
    };
    made.map(|bpe| Counter::Bpe { name: label, bpe: Box::new(bpe) }).map_err(|error| Failure::Usage(format!("cannot load the {label} vocabulary: {error}")))
}

impl Counter {
    /// The counter for `--tokenizer` or `--model` (or `KRYPTON_MODEL`); `repo` is where the cache for Anthropic's counts lives.
    ///
    /// # Errors
    ///
    /// [`Failure::Usage`] for a model nobody knows, a missing `ANTHROPIC_API_KEY`, or a counter that cannot start.
    pub fn choose(tokenizer: Option<&str>, model: Option<&str>, repo: &Path) -> Result<Self, Failure> {
        if let Some(name) = tokenizer {
            return if name == "estimate" { Ok(Self::Estimate) } else { bpe(name) };
        }
        let model = model.map(ToString::to_string).or_else(|| env::var("KRYPTON_MODEL").ok()).filter(|name| !name.is_empty());
        let Some(model) = model else { return Ok(Self::Estimate) };
        let lower = model.to_lowercase();
        let openai_new = ["gpt-4o", "gpt-4.1", "gpt-5", "chatgpt-4o", "o1", "o3", "o4"];
        let openai_old = ["gpt-4", "gpt-3.5", "text-embedding-3"];
        if lower.starts_with("claude") {
            Anthropic::start(&model, repo).map(|counter| Self::Anthropic(Box::new(counter)))
        } else if openai_new.iter().any(|prefix| lower.starts_with(prefix)) {
            bpe("o200k_base")
        } else if openai_old.iter().any(|prefix| lower.starts_with(prefix)) {
            bpe("cl100k_base")
        } else {
            Err(unknown(&model))
        }
    }

    /// What the command prints about where the numbers came from.
    #[must_use]
    pub fn describe(&self) -> String {
        match self {
            Self::Estimate => {
                format!("estimate, {CHARS_PER_TOKEN} characters per token (name your model with --model, or give --tokens-from, for real counts)")
            }
            Self::Bpe { name, .. } => format!("{name}, counted here"),
            Self::Anthropic(counter) => format!("Anthropic's token counter for {} (file text goes to Anthropic, not to Krypton)", counter.model),
        }
    }

    /// Tokens in `text`.
    ///
    /// # Errors
    ///
    /// [`Failure::Server`] when Anthropic's counter refuses or cannot be reached (no estimate is substituted).
    pub fn count(&mut self, text: &str) -> Result<u64, Failure> {
        match self {
            Self::Estimate => Ok(estimate(text)),
            Self::Bpe { bpe, .. } => Ok(u64::try_from(bpe.encode_ordinary(text).len()).unwrap_or(u64::MAX)),
            Self::Anthropic(counter) => counter.count(text),
        }
    }

    /// Write back what the counter learned (Anthropic's answers, so a second run asks for nothing).
    pub fn save(&self) {
        if let Self::Anthropic(counter) = self {
            counter.save();
        }
    }
}

fn estimate(text: &str) -> u64 {
    #[allow(
        clippy::cast_precision_loss,
        clippy::cast_possible_truncation,
        clippy::cast_sign_loss,
        reason = "a file's length in characters is far below 2^52, and the estimate is never negative"
    )]
    let tokens = (text.chars().count() as f64 / CHARS_PER_TOKEN).round_ties_even() as u64;
    tokens
}

fn digest(model: &str, text: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(model.as_bytes());
    hash.update([0]);
    hash.update(text.as_bytes());
    krypton_client::hex(&hash.finalize())
}

impl Anthropic {
    fn start(model: &str, repo: &Path) -> Result<Self, Failure> {
        let key = env::var("ANTHROPIC_API_KEY").ok().filter(|key| !key.is_empty()).ok_or_else(|| {
            Failure::Usage(format!(
                "counting for {model} asks Anthropic's token counter: set ANTHROPIC_API_KEY (or use --tokens-from, or --tokenizer for an estimate)"
            ))
        })?;
        eprintln!("counting tokens with Anthropic's token counter for {model}: each file's text goes to Anthropic (not to Krypton)");
        let cache_path = repo.join(STORE).join("tokens.json");
        let counts = fs::read_to_string(&cache_path)
            .ok()
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .and_then(|stored| stored.get("counts").cloned())
            .and_then(|counts| serde_json::from_value(counts).ok())
            .unwrap_or_default();
        let mut counter = Self { key, model: model.to_string(), overhead: 0, cache_path, counts };
        counter.overhead = counter.ask("a")?.saturating_sub(1);
        Ok(counter)
    }

    fn ask(&self, text: &str) -> Result<u64, Failure> {
        let body = json!({ "model": self.model, "messages": [{ "role": "user", "content": text }] });
        let reply = ureq::post(COUNT_URL)
            .timeout(Duration::from_secs(60))
            .set("x-api-key", &self.key)
            .set("anthropic-version", "2023-06-01")
            .set("content-type", "application/json")
            .send_string(&body.to_string());
        let answer = match reply {
            Ok(reply) => reply.into_string().map_err(|error| Failure::Server(format!("cannot read Anthropic's answer: {error}")))?,
            Err(ureq::Error::Status(code, reply)) => {
                let text = reply.into_string().unwrap_or_default();
                return Err(Failure::Server(format!("Anthropic's token counter refused the request ({code}): {}", text.chars().take(400).collect::<String>())));
            }
            Err(ureq::Error::Transport(problem)) => return Err(Failure::Server(format!("could not reach Anthropic's token counter: {problem}"))),
        };
        serde_json::from_str::<Value>(&answer)
            .ok()
            .and_then(|value| value.get("input_tokens").and_then(Value::as_u64))
            .ok_or_else(|| Failure::Server("Anthropic's token counter answered without input_tokens".to_string()))
    }

    fn count(&mut self, text: &str) -> Result<u64, Failure> {
        if text.is_empty() {
            return Ok(0);
        }
        let key = digest(&self.model, text);
        if let Some(known) = self.counts.get(&key) {
            return Ok(*known);
        }
        let counted = self.ask(text)?.saturating_sub(self.overhead).max(1);
        self.counts.insert(key, counted);
        Ok(counted)
    }

    fn save(&self) {
        let stored = json!({ "model": self.model, "counts": self.counts });
        let written = self.cache_path.parent().map(crate::runlog::ensure_ignored).transpose().and_then(|_| fs::write(&self.cache_path, stored.to_string()));
        if let Err(error) = written {
            eprintln!("could not save the token cache {}: {error}", self.cache_path.display());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The openai vocabularies count known text.
    #[test]
    fn test_openai_counts() {
        // the example in OpenAI's own tokenizer documentation: "tiktoken is great!" is 6 tokens in cl100k_base
        let mut counter = bpe("cl100k_base").unwrap();
        assert_eq!(counter.count("tiktoken is great!").unwrap(), 6);
        assert_eq!(counter.count("").unwrap(), 0);
        let mut newer = bpe("o200k_base").unwrap();
        assert_eq!(newer.count("hello world").unwrap(), 2);
    }

    // An openai model picks its vocabulary.
    #[test]
    fn test_model_vocabulary() {
        let here = Path::new(".");
        assert!(matches!(Counter::choose(None, Some("gpt-4o-mini"), here), Ok(Counter::Bpe { name: "o200k_base", .. })));
        assert!(matches!(Counter::choose(None, Some("gpt-4-turbo"), here), Ok(Counter::Bpe { name: "cl100k_base", .. })));
    }

    // An estimate can be asked for and an unknown model is refused.
    #[test]
    fn test_unknown_model() {
        let here = Path::new(".");
        assert!(matches!(Counter::choose(Some("estimate"), Some("anything"), here), Ok(Counter::Estimate)));
        assert!(matches!(Counter::choose(None, Some("mystery-1"), here), Err(Failure::Usage(_))));
    }
}
