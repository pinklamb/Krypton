//! Telling bundled libraries from a repository's own code, as `krypton/engine/vendored.py` does. The client works this out because it
//! needs the file's content; the result goes in the wire file's `flags.vendored`. The rule is ported exactly and tested against the
//! Python results.

#![allow(
    clippy::arithmetic_side_effects,
    clippy::string_slice,
    reason = "client side: offsets come from tree-sitter and from the length of the very text being sliced, so they sit on character boundaries and far below usize::MAX"
)]

use regex::{Regex, RegexBuilder};

const LIBRARIES: &[&str] = &[
    "bootstrap",
    "jquery",
    "jquery-ui",
    "jquery.ui",
    "lodash",
    "underscore",
    "moment",
    "popper",
    "angular",
    "d3",
    "chart",
    "backbone",
    "three",
    "highlight",
    "prism",
    "select2",
    "datatables",
    "fontawesome",
    "font-awesome",
    "swiper",
    "owl.carousel",
    "slick",
    "leaflet",
    "mathjax",
    "socket.io",
    "axios",
    "vue",
    "react",
    "react-dom",
    "ember",
    "knockout",
    "modernizr",
    "require",
    "bluebird",
    "handlebars",
    "mustache",
    "codemirror",
    "tinymce",
    "ckeditor",
    "echarts",
    "highcharts",
    "gsap",
    "sweetalert",
    "toastr",
    "dropzone",
    "flatpickr",
    "hammer",
    "fullcalendar",
];
const VARIANTS: &str = r"(?:[-._](?:min|slim|bundle|esm|umd|full|all|lite|dev|prod|production|development|standalone))*";
const VERSION: &str = r"(?:[-._]v?\d+(?:\.\d+)+)";
const ASSET_FOLDERS: &[&str] = &["static", "public", "assets", "wwwroot", "vendors", "plugins", "javascripts"];
const BUNDLER: &str = concat!(
    r#"__webpack_require__|webpackJsonp|webpackChunk|webpackUniversalModuleDefinition|parcelRequire|System\.register\("#,
    r#"|typeof\s+exports\s*={2,3}\s*['"]object['"][\s\S]{0,300}define\.amd|typeof\s+define\s*={2,3}\s*['"]function['"]\s*&&\s*define\.amd"#
);
const JS_SUFFIXES: &[&str] = &[".js", ".mjs", ".cjs", ".jsx"];
const HEAD: usize = 4000;
const TAIL: usize = 400;
const MINIFIED_AVERAGE: f64 = 200.0;
const MIN_MINIFIED_SIZE: usize = 2000;

pub struct Vendored {
    library: Regex,
    bundler: Regex,
    license: Regex,
    version_number: Regex,
    source_map: Regex,
    long_line: Regex,
}

fn compile(pattern: &str, ignore_case: bool) -> Result<Regex, regex::Error> {
    RegexBuilder::new(pattern).case_insensitive(ignore_case).build()
}

/// The first `count` characters.
fn char_prefix(text: &str, count: usize) -> &str {
    text.char_indices().nth(count).map_or(text, |(end, _)| &text[..end])
}

/// The last `count` characters.
fn char_suffix(text: &str, count: usize) -> &str {
    let total = text.chars().count();
    text.char_indices().nth(total.saturating_sub(count)).map_or("", |(start, _)| &text[start..])
}

impl Vendored {
    /// Compile every pattern once.
    ///
    /// # Errors
    ///
    /// A [`regex::Error`] when a built-in pattern does not compile. That is a bug in the client, not a problem with any input.
    pub fn new() -> Result<Self, regex::Error> {
        let names: Vec<String> = LIBRARIES.iter().map(|name| regex::escape(name)).collect();
        let library = format!(r"^(?P<name>{})(?P<version>{VERSION})?{VARIANTS}\.(?:js|mjs|cjs)$", names.join("|"));
        Ok(Self {
            library: compile(&library, true)?,
            bundler: compile(BUNDLER, false)?,
            license: compile(r"@license|copyright\s*\(c\)|licensed under|released under|\bMIT\b\s+licen[cs]e", true)?,
            version_number: compile(r"\bv?\d+\.\d+(?:\.\d+)?\b", false)?,
            source_map: compile(r"//[#@]\s*sourceMappingURL=", false)?,
            long_line: compile(r"[^\n]{1001,}", false)?,
        })
    }

    /// Minified shape: a big file with a very long line or a high average line length.
    fn minified(&self, text: &str) -> bool {
        let length = text.chars().count();
        #[allow(clippy::cast_precision_loss, reason = "a file's length is far below 2^52")]
        let average = length as f64 / (text.matches('\n').count() + 1) as f64;
        length > MIN_MINIFIED_SIZE && (self.long_line.is_match(text) || average > MINIFIED_AVERAGE)
    }

    /// A license banner with a version number, or a source-map comment at the end.
    fn banner(&self, text: &str) -> bool {
        let top = char_prefix(text, 2000);
        (self.license.is_match(top) && self.version_number.is_match(top)) || self.source_map.is_match(char_suffix(text, TAIL))
    }

    /// `vendored_name` or `vendored_content` when a JavaScript file looks like a bundled library, else None.
    #[must_use]
    pub fn reason(&self, filename: &str, text: &str) -> Option<&'static str> {
        if !JS_SUFFIXES.iter().any(|suffix| filename.to_lowercase().ends_with(suffix)) {
            return None;
        }
        let base = filename.rsplit('/').next().unwrap_or(filename);
        let library = self.library.captures(base);
        let named = library.is_some();
        let bundler = self.bundler.is_match(char_prefix(text, HEAD));
        let shape = self.minified(text);
        let folder = filename.rsplit_once('/').map_or("", |(directory, _)| directory).to_lowercase();
        let in_assets = folder.split('/').any(|part| ASSET_FOLDERS.contains(&part));
        let versioned = library.as_ref().is_some_and(|found| found.name("version").is_some());
        let backed = (bundler || shape) && self.banner(text);
        let strong = usize::from(named) + usize::from(bundler) + usize::from(shape);
        let weak = usize::from(versioned) + usize::from(in_assets) + usize::from(backed);
        (strong > 0 && strong + weak >= 2).then_some(if named { "vendored_name" } else { "vendored_content" })
    }
}
