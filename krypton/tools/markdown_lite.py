"""Just enough Markdown for SCORING.md: headings, paragraphs, lists, tables, code, rules.

Inline: `code`, **bold**, [links](target). Relative links get a prefix so they still
resolve from wherever the HTML page lives.
"""

from __future__ import annotations

import html
import re

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
ORDERED = re.compile(r"^\d+\.\s+")


def to_html(markdown: str, link_prefix: str = "") -> str:
    blocks, lines = [], markdown.splitlines()
    index = 0
    while index < len(lines):
        html_block, index = _block(lines, index, link_prefix)
        if html_block:
            blocks.append(html_block)
    return "\n".join(blocks)


def _block(lines: list[str], index: int, prefix: str) -> tuple[str, int]:
    """Render the block that starts at lines[index]; return it and the next index."""
    line = lines[index]
    if not line.strip():
        return "", index + 1
    if line.startswith("```"):
        return _fenced(lines, index)
    if line.startswith("#"):
        level = len(line) - len(line.lstrip("#"))
        text = line[level:].strip()
        return f'<h{level + 1} id="{_slug(text)}">{inline(text, prefix)}</h{level + 1}>', index + 1
    if line.strip() == "---":
        return "<hr>", index + 1
    if line.startswith("|"):
        return _table(lines, index, prefix)
    if line.startswith("- ") or ORDERED.match(line):
        return _list(lines, index, prefix)
    return _paragraph(lines, index, prefix)


def _fenced(lines: list[str], index: int) -> tuple[str, int]:
    end = index + 1
    while end < len(lines) and not lines[end].startswith("```"):
        end += 1
    code = html.escape("\n".join(lines[index + 1 : end]))
    return f"<pre><code>{code}</code></pre>", end + 1


def _table(lines: list[str], index: int, prefix: str) -> tuple[str, int]:
    rows = []
    while index < len(lines) and lines[index].startswith("|"):
        rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
        index += 1
    header, body = rows[0], [row for row in rows[1:] if not set("".join(row)) <= set("-: ")]
    head = "".join(f"<th>{inline(cell, prefix)}</th>" for cell in header)
    rows_html = "".join("<tr>" + "".join(f"<td>{inline(cell, prefix)}</td>" for cell in row) + "</tr>" for row in body)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows_html}</tbody></table>", index


def _list(lines: list[str], index: int, prefix: str) -> tuple[str, int]:
    tag = "ol" if ORDERED.match(lines[index]) else "ul"
    items = []
    while index < len(lines) and (lines[index].startswith("- ") or ORDERED.match(lines[index])):
        text = ORDERED.sub("", lines[index]) if tag == "ol" else lines[index][2:]
        index += 1
        while index < len(lines) and lines[index].startswith("  ") and lines[index].strip():
            text += " " + lines[index].strip()  # wrapped continuation line
            index += 1
        items.append(f"<li>{inline(text, prefix)}</li>")
    return f"<{tag}>{''.join(items)}</{tag}>", index


def _paragraph(lines: list[str], index: int, prefix: str) -> tuple[str, int]:
    text = []
    while index < len(lines) and lines[index].strip() and not _starts_block(lines[index]):
        text.append(lines[index].strip())
        index += 1
    return f"<p>{inline(' '.join(text), prefix)}</p>", index


def _starts_block(line: str) -> bool:
    return line.startswith(("#", "|", "```", "- ")) or bool(ORDERED.match(line)) or line.strip() == "---"


def inline(text: str, prefix: str = "") -> str:
    """Escape, then apply `code`, **bold** and [links]; code spans are protected first."""
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    text = html.escape(INLINE_CODE.sub(stash, text), quote=False)
    text = BOLD.sub(r"<strong>\1</strong>", text)
    text = LINK.sub(lambda match: _link(match, prefix), text)
    return re.sub(r"\x00(\d+)\x00", lambda match: spans[int(match.group(1))], text)


def _link(match: re.Match[str], prefix: str) -> str:
    target = match.group(2)
    if not target.startswith(("http://", "https://", "#", "/")):
        target = prefix + target
    return f'<a href="{html.escape(target)}">{match.group(1)}</a>'


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
