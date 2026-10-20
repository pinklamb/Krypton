"""Render a refactor-ladder results file as one self-contained HTML dashboard.

    python -m tools.ladder_report cache_anthropic_results/ladder_2026-10-20.json dashboard.html
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from tools.ladder_summary import summarize

COLORS = {"control": "#8a8f98", "standard": "#2f6fed", "targeted": "#12a594", "strict": "#c2410c", "relaxed": "#a855f7"}
W, H, PAD = 560, 260, 44


def nice_max(value: float) -> float:
    """Round an axis maximum up to 1, 2 or 5 times a power of ten."""
    if value <= 0:
        return 1.0
    scale = 10 ** (len(f"{int(value * 1e6)}") - 7)
    for step in (1, 2, 5, 10):
        if value <= step * scale:
            return step * scale
    return 10 * scale


def line_chart(series: dict[str, list[float]], xlabels: list[str], title: str) -> str:
    """An SVG line chart, one scale, a legend by direct end labels."""
    top = nice_max(max((v for vs in series.values() for v in vs), default=0))
    xs = [PAD + i * (W - 2 * PAD) / max(1, len(xlabels) - 1) for i in range(len(xlabels))]
    y = lambda v: H - PAD - v / top * (H - 2 * PAD)
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(title)}">']
    for tick in range(5):
        value = top * tick / 4
        parts.append(f'<line x1="{PAD}" x2="{W - PAD}" y1="{y(value):.1f}" y2="{y(value):.1f}" class="grid"/><text x="{PAD - 6}" y="{y(value) + 4:.1f}" class="tick" text-anchor="end">${value:.3g}</text>')
    parts += [f'<text x="{x:.1f}" y="{H - PAD + 18}" class="tick" text-anchor="middle">{html.escape(label)}</text>' for x, label in zip(xs, xlabels)]
    for name, values in series.items():
        points = " ".join(f"{x:.1f},{y(v):.1f}" for x, v in zip(xs, values))
        color = COLORS.get(name, "#555")
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        parts += [f'<circle cx="{x:.1f}" cy="{y(v):.1f}" r="3.5" fill="{color}"><title>{name}: ${v:.3f}</title></circle>' for x, v in zip(xs, values)]
    return "".join(parts) + "</svg>"


def bars(values: dict[str, float], unit: str = "") -> str:
    """Horizontal bars from zero, labelled with their values."""
    top = max(values.values(), default=0) or 1
    rows = [f'<div class="bar"><span>{html.escape(n)}</span><i style="width:{v / top * 100:.1f}%;background:{COLORS.get(n, "#555")}"></i><b>{v:.1f}{unit}</b></div>' for n, v in values.items()]
    return "".join(rows)


def legend(names) -> str:
    return "".join(f'<span class="key"><i style="background:{COLORS.get(n, "#555")}"></i>{n}</span>' for n in names)


def verdict(summary: dict) -> str:
    """One honest sentence per cleanup variant, from the measured numbers."""
    lines = []
    for name, row in summary["branches"].items():
        if name == "control":
            continue
        if row["saving_per_ticket"] > 0 and row["payback_tickets"]:
            lines.append(f"{name}: {row['saving_per_ticket'] * 100:.2f}¢ cheaper per ticket, cleanup of ${row['cleanup_cost']:.2f} pays back after about {row['payback_tickets']:.0f} tickets (cheaper in {row['cheaper_in']} of {row['reps']} repetitions).")
        else:
            lines.append(f"{name}: not cheaper per ticket here ({row['saving_per_ticket'] * 100:+.2f}¢), so the ${row['cleanup_cost']:.2f} cleanup did not pay back in these tickets.")
    return "<br>".join(html.escape(line) for line in lines)


def render(data: dict, note: str = "") -> str:
    """The whole page."""
    summary = summarize(data)
    if not summary["reps"]:
        return "<!doctype html><title>Krypton cost ladder</title><p>No complete repetitions in this file yet.</p>"
    rows, tasks = summary["branches"], data["meta"]["tasks"][1:]
    labels = [f"ticket {t}" for t in tasks]
    per_ticket = {n: r["per_ticket"] for n, r in rows.items()}
    cumulative = {}
    for n, r in rows.items():
        total, points = r["cleanup_cost"], []
        for cost in r["per_ticket"]:
            total += cost
            points.append(total)
        cumulative[n] = points
    table = "".join(
        f"<tr><td>{n}</td><td>${r['later_cost']:.3f}</td><td>{r['saving_per_ticket'] * 100:+.2f}¢</td><td>${r['cleanup_cost']:.2f}</td>"
        f"<td>{'%.0f tickets' % r['payback_tickets'] if r['payback_tickets'] else '–'}</td><td>{r['cheaper_in']}/{r['reps']}</td>"
        f"<td>{r['prompt_tokens_per_ticket'] / 1000:.0f}k</td><td>{r['requests_per_ticket']:.1f}</td><td>{r['suite_ok']}/{r['reps']}</td></tr>"
        for n, r in rows.items())
    meta = data["meta"]
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Krypton cost ladder</title>
<style>:root{{--bg:#fff;--fg:#14171f;--mut:#5b6270;--line:#e3e6ec;--card:#f6f7fa}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f1218;--fg:#e8eaf0;--mut:#9aa1b0;--line:#272c37;--card:#171b24}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;padding:24px 16px}}main{{max-width:1040px;margin:auto}}
h1{{margin:0 0 4px}}.sub{{color:var(--mut)}}.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:16px;margin-block:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}}h2{{font-size:16px;margin:0 0 8px}}
svg{{width:100%;height:auto}}.grid{{stroke:var(--line)}}.tick{{fill:var(--mut);font-size:11px}}.key{{margin-right:14px;font-size:13px}}.key i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}}
.bar{{display:grid;grid-template-columns:80px 1fr 64px;align-items:center;gap:8px;margin:6px 0}}.bar i{{height:12px;border-radius:2px}}.bar b{{font-variant-numeric:tabular-nums;text-align:right}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{text-align:right;padding:6px 10px;border-bottom:1px solid var(--line)}}th:first-child,td:first-child{{text-align:left}}
.note{{background:#fff3cd;color:#5c4400;padding:8px 12px;border-radius:8px}}</style>
<main><h1>Krypton cost ladder</h1>
<p class="sub">Is the next change cheaper for an agent after Krypton cleans the code? {summary["reps"]} complete repetition(s) · {meta['model']} · spent ${meta['spent_usd']:.2f} · shared first ticket ${summary['shared_cost']:.3f}</p>
{f'<p class="note">{html.escape(note)}</p>' if note else ''}
<div class="card"><h2>Verdict</h2><p>{verdict(summary)}</p></div>
<div class="grid2"><div class="card"><h2>Cost per ticket</h2>{legend(rows)}{line_chart(per_ticket, labels, 'Cost per ticket')}</div>
<div class="card"><h2>Cumulative cost, cleanup included</h2>{legend(rows)}{line_chart(cumulative, labels, 'Cumulative cost')}<p class="sub">Where a line crosses below control, the cleanup has paid for itself.</p></div>
<div class="card"><h2>Structure score after the last ticket (higher is better)</h2>{bars({n: r['purity_final'] for n, r in rows.items()})}</div>
<div class="card"><h2>Deeply nested functions after the last ticket</h2>{bars({n: r['nested_final'] for n, r in rows.items()})}</div></div>
<div class="card scroll"><h2>All branches</h2><table><tr><th>Branch</th><th>Tickets 2–{tasks[-1]} $</th><th>Saving / ticket</th><th>Cleanup $</th><th>Payback</th><th>Cheaper in</th><th>Prompt tokens / ticket</th><th>Requests / ticket</th><th>Suite passes</th></tr>{table}</table></div>
</main></html>"""


if __name__ == "__main__":
    out = Path(sys.argv[2])
    out.write_text(render(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))), encoding="utf-8")
    print(out)
