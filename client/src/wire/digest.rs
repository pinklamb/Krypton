//! The expression digest: the same 16 hex characters the Python engine computes for equal expressions.

use sha2::{Digest, Sha256};

use super::lines::collapsed;

/// sha256 of the whitespace-collapsed text, first 16 hex characters.
#[must_use]
pub fn expression_digest(text: &str) -> String {
    let hash = Sha256::digest(collapsed(text).as_bytes());
    hex(hash.get(..8).unwrap_or_default())
}

/// Bytes as lowercase hex.
#[must_use]
pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().flat_map(|byte| [byte >> 4, byte & 0xf]).filter_map(|nibble| char::from_digit(u32::from(nibble), 16)).collect()
}
