"""HTML pieces for the audit page: one function per section, all returning strings."""

from __future__ import annotations

import html
import re

from tools.collect import Entry, Field, Module

esc = html.escape

CSS = """
:root{--bg:#fafaf8;--panel:#fff;--ink:#1d1f23;--mute:#656b75;--line:#e3e3df;--accent:#5b4fd6;--code:#f2f2ee;
--A:#1f8a4c;--B:#4f9b2f;--C:#b7791f;--D:#c2410c;--F:#b91c1c;--ok:#1f8a4c;--warn:#b7791f;--bad:#b91c1c}
@media(prefers-color-scheme:dark){:root{--bg:#15161a;--panel:#1d1f25;--ink:#e8e8ea;--mute:#9aa0aa;--line:#2c2f37;--accent:#9d93ff;--code:#14151a;
--A:#4ade80;--B:#a3e635;--C:#fbbf24;--D:#fb923c;--F:#f87171;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171}}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}
code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}
code{background:var(--code);padding:1px 5px;border-radius:4px}
pre{background:var(--code);padding:12px 14px;border-radius:8px;overflow-x:auto;margin:8px 0}pre code{background:none;padding:0}
a{color:var(--accent)}h1,h2,h3,h4{line-height:1.25}h2{margin:2.2em 0 .6em;padding-top:.4em;border-top:1px solid var(--line)}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr)}
nav{position:sticky;top:0;height:100vh;overflow-y:auto;padding:18px 14px;border-right:1px solid var(--line);background:var(--panel);font-size:13.5px}
nav h1{font-size:17px;margin:0 0 4px}nav .sub{color:var(--mute);margin-bottom:12px}
nav a{display:block;padding:3px 8px;border-radius:5px;text-decoration:none;color:var(--ink)}nav a:hover{background:var(--code)}
nav .group{margin:14px 8px 4px;color:var(--mute);font-size:11.5px;text-transform:uppercase;letter-spacing:.06em}
main{padding:28px clamp(16px,4vw,48px) 80px;max-width:1150px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card b{display:block;font-size:24px}.card span{color:var(--mute);font-size:12.5px}
table{border-collapse:collapse;width:100%;margin:10px 0;background:var(--panel);font-size:14px}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}th{background:var(--code);font-weight:600}
.scroll{overflow-x:auto}
details.fn{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:6px 0}
details.fn>summary{cursor:pointer;padding:8px 12px;list-style:none;display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
details.fn>summary::-webkit-details-marker{display:none}details.fn[open]>summary{border-bottom:1px solid var(--line)}
details.fn .body{padding:8px 14px 12px}.kind{font-size:11px;color:var(--mute);text-transform:uppercase;min-width:52px}
.sum{color:var(--mute);flex:1 1 280px}.badge{font-size:11.5px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);white-space:nowrap}
.g-A{color:var(--A)}.g-B{color:var(--B)}.g-C{color:var(--C)}.g-D{color:var(--D)}.g-E,.g-F{color:var(--F)}
.warn{color:var(--warn)}.bad{color:var(--bad)}.ok{color:var(--ok)}.mute{color:var(--mute)}
.metrics{display:flex;flex-wrap:wrap;gap:6px 18px;margin:6px 0;color:var(--mute);font-size:13px}.metrics b{color:var(--ink)}
.pipeline{display:flex;flex-wrap:wrap;gap:6px;align-items:stretch;margin:12px 0}
.step{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px;flex:1 1 150px}
.step b{display:block;color:var(--accent)}.step span{font-size:13px;color:var(--mute)}
.search{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);font:inherit}
.tag-built{color:var(--ok)}.tag-amended{color:var(--warn)}.tag-switch{color:var(--warn)}.tag-phase1{color:var(--mute)}
.hidden{display:none}.note{background:var(--panel);border-left:4px solid var(--warn);padding:8px 14px;border-radius:0 8px 8px 0;margin:12px 0}
@media(max-width:820px){.layout{grid-template-columns:1fr}nav{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line)}}
"""

JS = """
const q=document.getElementById('q'),fns=[...document.querySelectorAll('details.fn')];
function filter(){const t=q.value.trim().toLowerCase();let n=0;
 for(const f of fns){const hit=!t||f.dataset.search.includes(t);f.classList.toggle('hidden',!hit);n+=hit;
  if(t&&hit&&n<=8)f.open=true;else if(!t)f.open=false;}
 document.getElementById('count').textContent=t?n+' match'+(n===1?'':'es'):fns.length+' entries';
 document.querySelectorAll('section.module').forEach(s=>s.classList.toggle('hidden',t&&!s.querySelector('details.fn:not(.hidden)')));}
q.addEventListener('input',filter);
document.getElementById('expand').onclick=()=>fns.forEach(f=>f.open=!f.classList.contains('hidden'));
document.getElementById('collapse').onclick=()=>fns.forEach(f=>f.open=false);
"""

SECTIONS = [
    ("overview", "Overview"), ("capabilities", "Capabilities"), ("pipeline", "Pipeline"),
    ("scoring", "Scoring rules"), ("cli", "Command line"), ("config", "Configuration"),
    ("models", "Data models"), ("sample", "Sample run"), ("spec", "Spec coverage"),
    ("modules", "Module reference"), ("tests", "Tests"), ("audit", "Audit notes"),
]


def page(title: str, body: str, modules: list[Module]) -> str:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
        f"<style>{CSS}</style></head><body><div class=\"layout\">{_nav(title, modules)}"
        f"<main>{body}</main></div><script>{JS}</script></body></html>"
    )


def _nav(title: str, modules: list[Module]) -> str:
    links = "".join(f'<a href="#{key}">{esc(name)}</a>' for key, name in SECTIONS)
    mods = "".join(f'<a href="#{slug(module.path)}">{esc(module.path.removeprefix("argon/"))}</a>' for module in modules)
    return f'<nav><h1>{esc(title)}</h1><div class="sub">audit reference</div>{links}<div class="group">Modules</div>{mods}</nav>'


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cards(items: list[tuple[str, str]]) -> str:
    return '<div class="cards">' + "".join(f'<div class="card"><b>{esc(value)}</b><span>{esc(label)}</span></div>' for label, value in items) + "</div>"


def table(headers: list[str], rows: list[list[str]], raw: bool = False) -> str:
    """rows hold plain text, escaped here, unless raw=True (cells are already HTML)."""
    cell = (lambda text: text) if raw else (lambda text: esc(text))
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell(value)}</td>" for value in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def section(key: str, heading: str, content: str) -> str:
    return f'<section id="{key}"><h2>{esc(heading)}</h2>{content}</section>'


def bullets(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def pipeline(steps: list[tuple[str, str]]) -> str:
    cells = "".join(f'<div class="step"><b>{esc(name)}</b><span>{esc(detail)}</span></div>' for name, detail in steps)
    return f'<div class="pipeline">{cells}</div>'


def fields_table(fields: list[Field]) -> str:
    rows = [[f"<code>{esc(f.name)}</code>", f"<code>{esc(f.annotation)}</code>", f"<code>{esc(f.default)}</code>", esc(f.comment)] for f in fields]
    return table(["Field", "Type", "Default", "Note"], rows, raw=True)


def grade(rank: str) -> str:
    return f'<span class="g-{rank}">{rank}</span>'


def entry_card(module: Module, entry: Entry) -> str:
    key = f"{slug(module.path)}-{slug(entry.qualname)}"
    search = f"{entry.qualname} {entry.summary} {module.path}".lower()
    badges = _badges(entry)
    metrics = _metrics(entry)
    doc = f"<p>{esc(entry.doc)}</p>" if entry.doc else '<p class="bad">No docstring.</p>'
    body = (
        f'<div class="body"><pre><code>{esc(entry.signature)}</code></pre>{doc}{metrics}'
        f'<p class="mute">{esc(module.path)} lines {entry.start}-{entry.end}</p>'
        f"<details><summary>Source</summary><pre><code>{esc(entry.source)}</code></pre></details></div>"
    )
    return (
        f'<details class="fn" id="{key}" data-search="{esc(search, quote=True)}"><summary>'
        f'<span class="kind">{entry.kind}</span><code>{esc(entry.qualname)}</code>{badges}'
        f'<span class="sum">{esc(entry.summary)}</span></summary>{body}</details>'
    )


def _badges(entry: Entry) -> str:
    parts = [f'<span class="badge">radon {grade(entry.rank)} ({entry.complexity})</span>'] if entry.rank != "-" else []
    if entry.score is not None:
        parts.append(f'<span class="badge">score {entry.score}</span>')
    if entry.nested:
        parts.append('<span class="badge warn">nested</span>')
    if not entry.doc:
        parts.append('<span class="badge bad">no docstring</span>')
    return "".join(parts)


def _metrics(entry: Entry) -> str:
    if entry.score is None and entry.rank == "-":
        return ""
    cells = [f"radon complexity <b>{entry.complexity}</b> (grade {grade(entry.rank)})"]
    if entry.score is not None:
        cells += [f"Krypton score <b>{entry.score}</b>", f"branches <b>{entry.branches}</b>", f"depth <b>{entry.depth}</b>"]
    return '<div class="metrics">' + "".join(f"<span>{cell}</span>" for cell in cells) + "</div>"


def module_section(module: Module) -> str:
    cards_html = "".join(entry_card(module, entry) for entry in module.entries)
    doc = f"<p>{esc(module.doc)}</p>" if module.doc else ""
    head = f'<h3>{esc(module.path)} <span class="mute">({module.lines} lines, {len(module.entries)} definitions)</span></h3>'
    return f'<section class="module" id="{slug(module.path)}">{head}{doc}{cards_html}</section>'
