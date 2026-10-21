"""Telling a repository's own code from bundled libraries that were copied into it.

A copy of bootstrap.js or a webpack bundle is not the team's code, and scoring it only makes the repository look tangled.
Path rules catch the usual folders (see `vendored_path_globs`); this module catches the rest from the file's name and
content. A file is left out only when it shows a strong sign (a well-known library name, a bundler fingerprint, minified
shape) together with a second sign. A license banner or an assets folder on its own never hides a file, so first-party
code stays scored.
"""

from __future__ import annotations

import posixpath
import re

LIBRARIES = (
    "bootstrap", "jquery", "jquery-ui", "jquery.ui", "lodash", "underscore", "moment", "popper", "angular", "d3", "chart", "backbone", "three",
    "highlight", "prism", "select2", "datatables", "fontawesome", "font-awesome", "swiper", "owl.carousel", "slick", "leaflet", "mathjax",
    "socket.io", "axios", "vue", "react", "react-dom", "ember", "knockout", "modernizr", "require", "bluebird", "handlebars", "mustache",
    "codemirror", "tinymce", "ckeditor", "echarts", "highcharts", "gsap", "sweetalert", "toastr", "dropzone", "flatpickr", "hammer", "fullcalendar",
)
VARIANTS = r"(?:[-._](?:min|slim|bundle|esm|umd|full|all|lite|dev|prod|production|development|standalone))*"
VERSION = r"(?:[-._]v?\d+(?:\.\d+)+)"
LIBRARY_NAME = re.compile(rf"^(?P<name>{'|'.join(re.escape(name) for name in LIBRARIES)})(?P<version>{VERSION})?{VARIANTS}\.(?:js|mjs|cjs)$", re.IGNORECASE)
ASSET_FOLDERS = frozenset({"static", "public", "assets", "wwwroot", "vendors", "plugins", "javascripts"})
BUNDLER = re.compile(
    r"__webpack_require__|webpackJsonp|webpackChunk|webpackUniversalModuleDefinition|parcelRequire|System\.register\("
    r"|typeof\s+exports\s*={2,3}\s*['\"]object['\"][\s\S]{0,300}define\.amd|typeof\s+define\s*={2,3}\s*['\"]function['\"]\s*&&\s*define\.amd"
)
LICENSE = re.compile(r"@license|copyright\s*\(c\)|licensed under|released under|\bMIT\b\s+licen[cs]e", re.IGNORECASE)
VERSION_NUMBER = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")
SOURCE_MAP = re.compile(r"//[#@]\s*sourceMappingURL=")
LONG_LINE = re.compile(r"[^\n]{1001,}")
JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx")
HEAD, TAIL, MINIFIED_AVERAGE, MIN_MINIFIED_SIZE = 4000, 400, 200, 2000


def looks_vendored(filename: str, text: str) -> str | None:
    """`vendored_name` or `vendored_content` when a JavaScript file looks like a bundled library, else None."""
    if not filename.lower().endswith(JS_SUFFIXES):
        return None
    head = text[:HEAD]
    match = LIBRARY_NAME.match(posixpath.basename(filename))
    name = match is not None
    bundler = BUNDLER.search(head) is not None
    shape = len(text) > MIN_MINIFIED_SIZE and (LONG_LINE.search(text) is not None or len(text) / (text.count("\n") + 1) > MINIFIED_AVERAGE)
    weak = sum((
        match is not None and match.group("version") is not None,   # a version number in the name
        bool(ASSET_FOLDERS.intersection(posixpath.dirname(filename).lower().split("/"))),
        (bundler or shape) and ((LICENSE.search(head[:2000]) is not None and VERSION_NUMBER.search(head[:2000]) is not None) or SOURCE_MAP.search(text[-TAIL:]) is not None),   # a banner only backs up a bundle or minified code
    ))
    strong = name + bundler + shape
    if strong and strong + weak >= 2:
        return "vendored_name" if name else "vendored_content"
    return None


def linguist_globs(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(vendored globs, generated globs) from a .gitattributes file's linguist-vendored / linguist-generated lines."""
    vendored, generated = [], []
    for raw in text.splitlines():
        parts = raw.split("#", 1)[0].split()
        if len(parts) < 2:
            continue
        pattern, attributes = parts[0], parts[1:]
        for attribute in attributes:
            name, _, value = attribute.partition("=")
            if name not in ("linguist-vendored", "linguist-generated") or value.lower() == "false":
                continue
            target = vendored if name == "linguist-vendored" else generated
            target.extend(_glob_for(pattern))
    return tuple(vendored), tuple(generated)


def _glob_for(pattern: str) -> list[str]:
    """The engine globs for one .gitattributes pattern (a bare name matches at any depth; a trailing slash is a folder)."""
    anchored = pattern.startswith("/")
    body = pattern.strip("/")
    if not body:
        return []
    prefix = "" if anchored or "/" in body else "**/"
    return [f"{prefix}{body}", f"{prefix}{body}/**"]
