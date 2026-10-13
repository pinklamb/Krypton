"""Change-frequency index: how often each file changes, 0.0 (rock stable) to 1.0 (volatile).

Pure data in, pure data out: the CLI reads git history (or a JSON file) and passes the counts
here, so the engine stays free of I/O. The index never changes a score; it orders files so a
prompt builder can put stable files first and keep a long, cacheable prefix.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping

from krypton.schemas import FileFinding

CHARS_PER_TOKEN = 3.5  # a rough estimate; no vendor tokenizer is bundled


def change_frequency(
    counts: Mapping[str, int],
    files: Iterable[str],
    in_flux: Collection[str] = (),
) -> dict[str, float]:
    """Index each scanned file by its change count relative to the busiest scanned file.

    `counts` maps a path to the number of commits that touched it. A file with no history
    is 0.0, unless it is in `in_flux` (uncommitted or new), which is by definition 1.0.
    """
    files = list(files)
    busiest = max((counts.get(name, 0) for name in files), default=0)
    return {name: _index(counts.get(name, 0), busiest, name in in_flux) for name in files}


def _index(count: int, busiest: int, in_flux: bool) -> float:
    """One file's index: in-flux files are 1.0, otherwise count / busiest (0.0 if nothing changed)."""
    if in_flux:
        return 1.0
    return round(count / busiest, 4) if busiest else 0.0


def order_by_volatility(frequency: Mapping[str, float]) -> list[str]:
    """Filenames from most stable to most volatile; ties broken by name for a reproducible prefix."""
    return sorted(frequency, key=lambda name: (frequency[name], name))


def manifest(findings: Iterable[FileFinding], sizes: Mapping[str, int]) -> dict:
    """The --order-by-volatility JSON: scanned files, most stable first, with a size estimate each.

    `sizes` maps a path to its length in characters. A prompt builder that concatenates files
    in this order keeps the stable ones as a shared prefix, so a provider's prompt cache can
    reuse it between turns. This is a hint from history, not a guarantee of cache hits.
    """
    by_name = {finding.filename: finding for finding in findings}
    order = order_by_volatility({name: finding.change_frequency or 0.0 for name, finding in by_name.items()})
    return {
        "order": "most stable first",
        "files": [
            {
                "filename": name,
                "change_frequency": by_name[name].change_frequency,
                "flagged": by_name[name].flagged,
                "tokens_estimate": round(sizes.get(name, 0) / CHARS_PER_TOKEN),
            }
            for name in order
        ],
    }
