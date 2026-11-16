//! The request body of POST /v1/score, written as the server's `wire_schema.json` describes it (wire version 1).

use std::collections::BTreeMap;

use serde::Serialize;
use serde_json::Value;

pub const WIRE_VERSION: u32 = 1;
pub const NAMED: u8 = 1;
pub const EXTRA: u8 = 2;
pub const ERROR: u8 = 4;
pub const MISSING: u8 = 8;

/// One file's tree as parallel arrays in depth-first pre-order, every node present.
#[derive(Debug, Default, Serialize)]
pub struct WireTree {
    pub n: usize,
    pub kinds: Vec<String>,
    pub fields: Vec<String>,
    pub kind: Vec<u32>,
    pub field: Vec<i32>,
    pub parent: Vec<i64>,
    pub flags: Vec<u8>,
    pub start_line: Vec<u32>,
    pub start_col: Vec<u32>,
    pub start_index: Vec<u32>,
    pub end_line: Vec<u32>,
    pub end_col: Vec<u32>,
    pub end_index: Vec<u32>,
    /// node index -> a stand-in that keeps one fact (`_`/`?` for a match pattern, `n.n` for a dotted name).
    pub mask: BTreeMap<String, String>,
    /// node index -> 16-hex digest of a name or of an expression the engine only compares; never the text.
    pub digest: BTreeMap<String, String>,
}

/// A waiver comment as facts: where it is and its reason's digest, never the comment.
#[derive(Debug, Serialize)]
pub struct WireWaiver {
    pub comment_only: bool,
    pub reason: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct WireFlags {
    pub generated_marker: bool,
    pub vendored: Option<&'static str>,
}

#[derive(Debug, Serialize)]
pub struct WireFile {
    pub path: String,
    pub language: &'static str,
    pub bytes: usize,
    pub longest_line: usize,
    pub lines: Vec<super::lines::LineShape>,
    pub cr: Vec<usize>,
    pub final_newline: bool,
    pub flags: WireFlags,
    pub altered_lines: Option<Vec<u32>>,
    pub change_frequency: Option<f64>,
    pub waivers: BTreeMap<String, WireWaiver>,
    pub imports: Vec<Vec<String>>,
    pub tree: WireTree,
}

#[derive(Debug, Serialize)]
pub struct ClientInfo {
    pub name: &'static str,
    pub version: &'static str,
}

#[derive(Debug, Serialize)]
pub struct WireOptions {
    pub level: String,
    pub detail: String,
    pub config: Value,
}

#[derive(Debug, Serialize)]
pub struct WireRequest {
    pub wire_version: u32,
    pub client: ClientInfo,
    pub options: WireOptions,
    pub files: Vec<WireFile>,
}
