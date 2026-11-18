//! The name key: what makes a name's digest impossible to guess.
//!
//! A plain hash of a name can be reversed by hashing guesses (`get`, `user_id`, ...) and comparing. So every name and compared expression is
//! digested with HMAC-SHA256 under a 256-bit key that is made on the user's machine and never sent. Equal names still get equal digests inside
//! a request, which is all the server needs to compare them; without the key nobody can test a guess.
//!
//! The one exception is [`PUBLIC_WORDS`]: the few framework names the server looks for by name (event listeners, task decorators, socket
//! constructors). They keep the plain [`expression_digest`] so the server can recognise them; they are public API names, never the user's own.

use sha2::{Digest, Sha256};

use super::digest::{expression_digest, hex};
use super::lines::collapsed;

/// The framework names the server recognises, exactly as its walk lists them. A digest of anything else is keyed.
pub const PUBLIC_WORDS: [&str; 24] = [
    "on",
    "once",
    "addListener",
    "addEventListener",
    "subscribe",
    "consume",
    "process",
    "onSnapshot",
    "WebSocket",
    "WebSocketServer",
    "EventSource",
    "Worker",
    "basic_consume",
    "add_listener",
    "add_event_handler",
    "task",
    "shared_task",
    "websocket",
    "on_event",
    "subscriber",
    "KafkaConsumer",
    "AIOKafkaConsumer",
    "Consumer",
    "WebSocketApp",
];
/// Recognised by the server for `x.onmessage = ...`.
const ONMESSAGE: &str = "onmessage";
const BLOCK: usize = 64;

/// A 256-bit secret that stays on the client.
#[derive(Clone)]
pub struct NameKey([u8; 32]);

/// What is wrong with a key given as text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BadKey;

impl std::fmt::Display for BadKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("a name key is 64 hexadecimal characters")
    }
}

impl NameKey {
    #[must_use]
    pub const fn new(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }

    /// A key written as 64 hex characters (what `.krypton/key` holds).
    ///
    /// # Errors
    ///
    /// [`BadKey`] when the text is not exactly 64 hexadecimal characters.
    pub fn from_hex(text: &str) -> Result<Self, BadKey> {
        let text = text.trim();
        if text.len() != 64 || !text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(BadKey);
        }
        let mut bytes = [0_u8; 32];
        for (slot, pair) in bytes.iter_mut().zip(text.as_bytes().chunks(2)) {
            let pair = std::str::from_utf8(pair).map_err(|_bad| BadKey)?;
            *slot = u8::from_str_radix(pair, 16).map_err(|_bad| BadKey)?;
        }
        Ok(Self(bytes))
    }

    /// The key as 64 hex characters.
    #[must_use]
    pub fn to_hex(&self) -> String {
        hex(&self.0)
    }

    /// The digest the server receives for this text: keyed, unless it is one of the [`PUBLIC_WORDS`].
    #[must_use]
    pub fn digest(&self, text: &str) -> String {
        let collapsed = collapsed(text);
        if collapsed == ONMESSAGE || PUBLIC_WORDS.contains(&collapsed.as_str()) {
            return expression_digest(text);
        }
        let mac = hmac_sha256(&self.0, collapsed.as_bytes());
        hex(mac.get(..8).unwrap_or_default())
    }
}

/// HMAC-SHA256 (RFC 2104) with a 32-byte key, which is shorter than the 64-byte block and so is used as is, padded with zeros.
#[must_use]
pub fn hmac_sha256(key: &[u8; 32], message: &[u8]) -> [u8; 32] {
    let mut padded = [0_u8; BLOCK];
    for (slot, byte) in padded.iter_mut().zip(key) {
        *slot = *byte;
    }
    let inner_pad: Vec<u8> = padded.iter().map(|byte| byte ^ 0x36).collect();
    let outer_pad: Vec<u8> = padded.iter().map(|byte| byte ^ 0x5c).collect();
    let inner = Sha256::new().chain_update(&inner_pad).chain_update(message).finalize();
    Sha256::new().chain_update(&outer_pad).chain_update(inner).finalize().into()
}
